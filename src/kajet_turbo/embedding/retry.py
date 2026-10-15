"""Bounded, retried query embedding for interactive search.

A search is waiting on the query embedding, so a provider stall must not hold it for the
client's 30 s timeout. Each attempt gets a hard wall-clock deadline (``asyncio.timeout``):
httpx2 timeouts are per phase, so a slow-drip body can keep resetting the read timer and
never trip ``Timeout(read=...)`` on its own. A transient failure is retried exactly once;
if that fails too, search degrades to keyword-only.

The worker/indexing path does not use this: background latency doesn't matter there, and
long documents legitimately take longer than a query deadline.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx2

from kajet_turbo.embedding.base import QueryEmbedder
from kajet_turbo.log import logger
from kajet_turbo.perf import incr

# Transport failures worth one more try. httpcore2's own retries cover only connect
# errors, so a stale keep-alive socket (RemoteProtocolError) or a stall never gets a
# second attempt below us.
_TRANSIENT_ERRORS = (
    TimeoutError,
    httpx2.TimeoutException,
    httpx2.ConnectError,
    httpx2.RemoteProtocolError,
)


class EmbeddingUnavailable(Exception):
    """Every attempt at a query embedding failed transiently. ``__cause__`` is the last
    attempt's error."""

    def __init__(self, backend: str, attempts: int):
        super().__init__(f"Embedding backend {backend} unavailable after {attempts} attempts")
        self.backend = backend
        self.attempts = attempts


def is_transient(exc: BaseException) -> bool:
    """Rate limiting, server errors, timeouts and dropped connections. Credential and
    request rejections arrive as their own exception types and are never retried; nor is
    any other 4xx."""
    if isinstance(exc, httpx2.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(exc, _TRANSIENT_ERRORS)


@dataclass(frozen=True, slots=True)
class QueryEmbedPolicy:
    deadline_s: float = 2.0
    max_attempts: int = 2
    backoff_s: tuple[float, float] = (0.05, 0.25)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {self.max_attempts}")


DEFAULT_QUERY_POLICY = QueryEmbedPolicy()


class RetryingQueryEmbedder:
    """Wraps a ``QueryEmbedder`` with a per-attempt deadline and bounded retry. Worst case
    under the default policy: two 2 s attempts plus a sub-second jittered backoff."""

    def __init__(
        self,
        inner: QueryEmbedder,
        backend: str,
        policy: QueryEmbedPolicy = DEFAULT_QUERY_POLICY,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._inner = inner
        self._backend = backend
        self._policy = policy
        self._sleep = sleep

    async def embed_query(self, text: str) -> list[float]:
        policy = self._policy
        for attempt in range(1, policy.max_attempts + 1):
            # Counted at the start, so an attempt cut off by the deadline still shows.
            incr("embed_attempts")
            try:
                async with asyncio.timeout(policy.deadline_s):
                    return await self._inner.embed_query(text)
            except Exception as exc:
                if not is_transient(exc):
                    raise
                if attempt == policy.max_attempts:
                    raise EmbeddingUnavailable(self._backend, attempt) from exc
                incr("embed_retries")
                logger.warning(
                    "embed_query_retry",
                    backend=self._backend,
                    attempt=attempt,
                    reason=type(exc).__name__,
                )
            await self._sleep(random.uniform(*policy.backoff_s))
        raise AssertionError("unreachable: the last attempt returns or raises")
