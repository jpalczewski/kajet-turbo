import asyncio
import json

import httpx
import pytest

from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.openai_compat import OpenAICompatEmbedder

_CFG = EmbedderConfig(
    backend_id="openai-small",
    type="openai",
    model="text-embedding-3-small",
    dim=3,
    base_url="https://api.openai.com/v1",
    query_prefix="q: ",
    passage_prefix="p: ",
    api_key="sk-test",
)


def _embedder(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatEmbedder(_CFG, client), client


def _ok(request):
    body = json.loads(request.content)
    data = [{"index": i, "embedding": [0.1, 0.2, 0.3]} for i in range(len(body["input"]))]
    return httpx.Response(200, json={"data": data})


def test_embed_documents_prefixes_and_parses():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        captured["url"] = str(request.url)
        return _ok(request)

    emb, client = _embedder(handler)
    try:
        vecs = asyncio.run(emb.embed_documents(["alpha", "beta"]))
    finally:
        asyncio.run(client.aclose())

    assert vecs == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert captured["body"]["input"] == ["p: alpha", "p: beta"]
    assert captured["body"]["model"] == "text-embedding-3-small"
    assert captured["auth"] == "Bearer sk-test"
    assert captured["url"].endswith("/v1/embeddings")


def test_embed_query_uses_query_prefix_and_returns_single():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return _ok(request)

    emb, client = _embedder(handler)
    try:
        vec = asyncio.run(emb.embed_query("co to jest"))
    finally:
        asyncio.run(client.aclose())

    assert vec == [0.1, 0.2, 0.3]
    assert captured["body"]["input"] == ["q: co to jest"]


def test_batches_in_chunks_of_100():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(len(body["input"]))
        return _ok(request)

    emb, client = _embedder(handler)
    try:
        vecs = asyncio.run(emb.embed_documents([f"t{i}" for i in range(150)]))
    finally:
        asyncio.run(client.aclose())

    assert len(vecs) == 150
    assert calls == [100, 50]


def test_truncates_overlong_input():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return _ok(request)

    emb, client = _embedder(handler)
    try:
        asyncio.run(emb.embed_documents(["x" * 9000]))
    finally:
        asyncio.run(client.aclose())

    # "p: " prefix + truncated body, capped at the 8000-char guard
    assert len(captured["body"]["input"][0]) == 8000


def test_dim_mismatch_raises():
    def handler(request):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    emb, client = _embedder(handler)
    try:
        with pytest.raises(ValueError, match="dim"):
            asyncio.run(emb.embed_query("x"))
    finally:
        asyncio.run(client.aclose())


def test_empty_documents_makes_no_request():
    def handler(request):  # pragma: no cover - must never be called
        raise AssertionError("no HTTP call expected for empty input")

    emb, client = _embedder(handler)
    try:
        assert asyncio.run(emb.embed_documents([])) == []
    finally:
        asyncio.run(client.aclose())
