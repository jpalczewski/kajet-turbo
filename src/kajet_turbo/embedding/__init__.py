"""Embedding subsystem: port, adapters, registry, resolver, content cache, and the
factory that builds an adapter from a resolved EmbedderConfig."""

import httpx

from kajet_turbo.embedding.base import Embedder, EmbedderConfig
from kajet_turbo.embedding.openai_compat import OpenAICompatEmbedder


def build_embedder(config: EmbedderConfig, client: httpx.AsyncClient) -> Embedder:
    """Construct the adapter for a resolved backend config. HF adapter arrives in Plan 6."""
    if config.type == "openai":
        return OpenAICompatEmbedder(config, client)
    raise ValueError(f"unknown embedder type: {config.type!r}")
