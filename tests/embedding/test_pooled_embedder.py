import asyncio
import json

import httpx

from kajet_turbo.embedding import pooled_embedder_factory
from kajet_turbo.embedding.base import EmbedderConfig

_CFG = EmbedderConfig(
    backend_id="b", type="openai", model="m", dim=3, base_url="http://h/v1", api_key="k"
)


def test_pooled_embedder_embeds_and_closes_client():
    closed = {"count": 0}

    def handler(request):
        body = json.loads(request.content)
        data = [{"index": i, "embedding": [0.1, 0.2, 0.3]} for i in range(len(body["input"]))]
        return httpx.Response(200, json={"data": data})

    class _TrackingClient(httpx.AsyncClient):
        async def aclose(self):
            closed["count"] += 1
            await super().aclose()

    factory = pooled_embedder_factory(
        client_factory=lambda: _TrackingClient(transport=httpx.MockTransport(handler))
    )
    embedder = factory(_CFG)
    vecs = asyncio.run(embedder.embed_documents(["a", "b"]))
    assert vecs == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert closed["count"] == 1  # client closed after the call (no leak)


def test_pooled_embedder_query():
    def handler(request):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0]}]})

    factory = pooled_embedder_factory(
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    vec = asyncio.run(factory(_CFG).embed_query("q"))
    assert vec == [1.0, 2.0, 3.0]
