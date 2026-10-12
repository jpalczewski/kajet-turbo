import asyncio
import json

import httpx2
import pytest

from kajet_turbo.embedding import pooled_embedder_factory
from kajet_turbo.embedding.base import EmbedderConfig, EmbeddingAuthError, EmbeddingRequestRejected
from kajet_turbo.embedding.client import SharedEmbedderClient
from kajet_turbo.embedding.openai_compat import OpenAICompatEmbedder
from kajet_turbo.embedding.retry import (
    EmbeddingUnavailable,
    QueryEmbedPolicy,
    RetryingQueryEmbedder,
)
from kajet_turbo.log import setup_logging
from kajet_turbo.perf import perf_span
from tests.helpers import entries_named, read_log_entries

_VECTOR = [1.0, 0.0, 0.0]
_FAST = QueryEmbedPolicy(deadline_s=0.05)


def _status_error(status: int) -> httpx2.HTTPStatusError:
    request = httpx2.Request("POST", "http://embed.test/v1/embeddings")
    return httpx2.HTTPStatusError(
        f"HTTP {status}", request=request, response=httpx2.Response(status, request=request)
    )


class _Scripted:
    """Raises (or stalls on) each scripted outcome in turn, then returns a vector."""

    def __init__(self, *outcomes: BaseException | str):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        if self._outcomes:
            outcome = self._outcomes.pop(0)
            if outcome == "stall":
                await asyncio.sleep(10)
            elif isinstance(outcome, BaseException):
                raise outcome
        return _VECTOR


async def _no_sleep(_: float) -> None:
    pass


def _retrying(inner: _Scripted, policy: QueryEmbedPolicy = _FAST) -> RetryingQueryEmbedder:
    return RetryingQueryEmbedder(inner, "b", policy, sleep=_no_sleep)


@pytest.mark.parametrize(
    "first_failure",
    [
        "stall",
        httpx2.ReadTimeout("read timed out"),
        httpx2.ConnectError("refused"),
        httpx2.RemoteProtocolError("Server disconnected"),
        _status_error(429),
        _status_error(500),
        _status_error(503),
    ],
    ids=["deadline", "read-timeout", "connect", "stale-socket", "429", "500", "503"],
)
async def test_transient_failure_is_retried_once_and_succeeds(first_failure):
    inner = _Scripted(first_failure)

    assert await _retrying(inner).embed_query("q") == _VECTOR
    assert inner.calls == 2


@pytest.mark.parametrize(
    "failure",
    [
        EmbeddingAuthError("b", 401),
        EmbeddingRequestRejected("b", 404, "model not found"),
        _status_error(402),
        _status_error(413),
        ValueError("embedder returned dim 2, expected 3"),
        RuntimeError("bug"),
    ],
    ids=["auth", "rejected", "402", "413", "dim-mismatch", "unexpected"],
)
async def test_non_transient_failure_is_not_retried(failure):
    inner = _Scripted(failure)

    with pytest.raises(type(failure)) as raised:
        await _retrying(inner).embed_query("q")

    assert raised.value is failure
    assert inner.calls == 1


async def test_second_transient_failure_raises_unavailable_with_last_error():
    last = _status_error(503)
    inner = _Scripted("stall", last)

    with pytest.raises(EmbeddingUnavailable) as raised:
        await _retrying(inner).embed_query("q")

    assert raised.value.attempts == 2
    assert raised.value.__cause__ is last
    assert inner.calls == 2


async def test_non_transient_failure_on_the_retry_propagates_as_is():
    rejected = EmbeddingAuthError("b", 401)
    inner = _Scripted(httpx2.ConnectError("refused"), rejected)

    with pytest.raises(EmbeddingAuthError) as raised:
        await _retrying(inner).embed_query("q")

    assert raised.value is rejected


async def test_outer_cancellation_is_not_retried():
    inner = _Scripted("stall")
    task = asyncio.create_task(_retrying(inner, QueryEmbedPolicy(deadline_s=5.0)).embed_query("q"))
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert inner.calls == 1


async def test_backoff_sleeps_between_attempts_within_the_policy_bounds():
    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    policy = QueryEmbedPolicy(deadline_s=0.05, backoff_s=(0.1, 0.2))
    inner = _Scripted(httpx2.ConnectError("refused"))

    await RetryingQueryEmbedder(inner, "b", policy, sleep=record_sleep).embed_query("q")

    assert len(slept) == 1
    assert 0.1 <= slept[0] <= 0.2


def test_policy_rejects_zero_attempts():
    with pytest.raises(ValueError, match="max_attempts"):
        QueryEmbedPolicy(max_attempts=0)


async def test_attempts_and_retries_are_counted_and_the_retry_is_logged(capsys):
    setup_logging()
    capsys.readouterr()

    with perf_span() as perf:
        await _retrying(_Scripted(httpx2.RemoteProtocolError("Server disconnected"))).embed_query(
            "q"
        )

    assert perf is not None
    assert perf.fields["embed_attempts"] == 2
    assert perf.fields["embed_retries"] == 1
    (retry,) = entries_named(read_log_entries(capsys), "embed_query_retry")
    assert retry["level"] == "warning"
    assert retry["attempt"] == 1
    assert retry["reason"] == "RemoteProtocolError"


async def test_deadline_cancels_a_stalled_http_request_and_retries_through_the_same_client():
    """The one place asyncio.timeout really cancels an httpx2 request in flight; the
    phase timeouts are wide here so only the wall-clock deadline can cut the stall."""
    cfg = EmbedderConfig(backend_id="b", type="openai", model="m", dim=3, base_url="http://e/v1")
    requests = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            await asyncio.sleep(10)
        body = json.loads(request.content)
        return httpx2.Response(
            200, json={"data": [{"index": 0, "embedding": _VECTOR}], "model": body["model"]}
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=30.0) as client:
        embedder = RetryingQueryEmbedder(OpenAICompatEmbedder(cfg, client), "b", _FAST)
        loop = asyncio.get_running_loop()
        started = loop.time()

        assert await embedder.embed_query("q") == _VECTOR

    assert requests == 2
    assert loop.time() - started < 1.0


async def test_query_client_phase_timeouts_sit_under_the_deadline():
    holder = SharedEmbedderClient()
    timeout = holder.get().timeout
    await holder.aclose()

    deadline = QueryEmbedPolicy().deadline_s
    assert timeout.connect is not None and timeout.connect < deadline
    assert timeout.read is not None and timeout.read <= deadline
    assert timeout.pool is not None and timeout.pool < deadline


async def test_worker_embedding_path_keeps_its_long_timeout():
    cfg = EmbedderConfig(backend_id="b", type="openai", model="m", dim=3, base_url="http://e/v1")
    embedder = pooled_embedder_factory()(cfg)
    client = embedder._client_factory()  # the default client the worker actually uses
    await client.aclose()

    assert client.timeout.read == 30.0
    assert client.timeout.connect == 30.0
