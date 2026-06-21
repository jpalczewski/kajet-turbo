"""Embedding subsystem: port, adapters, registry, resolver, content cache, and the
factory that builds an adapter from a resolved EmbedderConfig."""

from collections.abc import Callable

import httpx

from kajet_turbo.embedding.base import Embedder, EmbedderConfig
from kajet_turbo.embedding.openai_compat import OpenAICompatEmbedder


def build_embedder(config: EmbedderConfig, client: httpx.AsyncClient) -> Embedder:
    """Construct the adapter for a resolved backend config. HF adapter arrives in Plan 6."""
    if config.type == "openai":
        return OpenAICompatEmbedder(config, client)
    raise ValueError(f"unknown embedder type: {config.type!r}")


class _PooledEmbedder:
    """Embedder wrapper for the sync→async bridge: opens and explicitly closes a fresh
    AsyncClient per call, inside the event loop that ``asyncio.run`` creates. A shared
    client cannot be reused across per-call loops, and a per-call client must be closed
    (``aclose`` in a finally) to avoid a socket leak."""

    def __init__(self, config: EmbedderConfig, client_factory: Callable[[], httpx.AsyncClient]):
        self._config = config
        self._client_factory = client_factory

    @property
    def name(self) -> str:
        return self._config.backend_id

    @property
    def dim(self) -> int:
        return self._config.dim

    @property
    def query_prefix(self) -> str:
        return self._config.query_prefix

    @property
    def passage_prefix(self) -> str:
        return self._config.passage_prefix

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        client = self._client_factory()
        try:
            return await build_embedder(self._config, client).embed_documents(texts)
        finally:
            await client.aclose()

    async def embed_query(self, text: str) -> list[float]:
        client = self._client_factory()
        try:
            return await build_embedder(self._config, client).embed_query(text)
        finally:
            await client.aclose()


def pooled_embedder_factory(
    client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> Callable[[EmbedderConfig], _PooledEmbedder]:
    """Build a ``NoteIndexer.build_embedder`` callable that produces per-call,
    self-closing embedders. ``client_factory`` defaults to a 30s-timeout AsyncClient;
    tests inject a MockTransport client."""
    factory = client_factory or (lambda: httpx.AsyncClient(timeout=30.0))
    return lambda config: _PooledEmbedder(config, factory)
