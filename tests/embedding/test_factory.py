import httpx
import pytest

from kajet_turbo.embedding import build_embedder
from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.openai_compat import OpenAICompatEmbedder


def test_build_openai_embedder():
    cfg = EmbedderConfig(
        backend_id="x",
        type="openai",
        model="m",
        dim=8,
        base_url="http://h/v1",
        api_key="k",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    emb = build_embedder(cfg, client)
    assert isinstance(emb, OpenAICompatEmbedder)


def test_unknown_type_raises():
    cfg = EmbedderConfig(
        backend_id="x",
        type="quantum",
        model="m",
        dim=8,
        base_url="http://h/v1",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(ValueError, match="unknown embedder type"):
        build_embedder(cfg, client)
