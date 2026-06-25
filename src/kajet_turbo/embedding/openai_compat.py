"""OpenAI-compatible embeddings adapter (POST {base_url}/embeddings).

One code path for OpenAI and any compatible gateway via ``base_url``. Async httpx
(network I/O — never ``run_sync``). Inputs are prefixed (passage/query), batched, and
char-truncated as a coarse token-limit guard before the request. The injected
``AsyncClient`` keeps the adapter testable with ``httpx.MockTransport``.
"""

import time

import httpx
from loguru import logger

from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.perf import incr, record

_BATCH = 100
# Coarse truncate guard, comfortably under typical 8k-token limits. MUST stay >= the
# chunker's hard_max (kajet_turbo.markdown.DEFAULT_HARD_MAX) so a normal chunk + its
# breadcrumb prefix is never silently truncated before embedding.
_MAX_CHARS = 8000


class OpenAICompatEmbedder:
    def __init__(self, config: EmbedderConfig, client: httpx.AsyncClient):
        self._config = config
        self._client = client

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
        return await self._embed([self.passage_prefix + t for t in texts])

    async def embed_query(self, text: str) -> list[float]:
        out = await self._embed([self.query_prefix + text])
        return out[0]

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []
        url = f"{self._config.base_url.rstrip('/')}/embeddings"
        headers = {}
        if self._config.api_key is not None:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        vectors: list[list[float]] = []
        truncated = 0
        for start in range(0, len(inputs), _BATCH):
            raw = inputs[start : start + _BATCH]
            batch = [t[:_MAX_CHARS] for t in raw]
            truncated += sum(1 for t in raw if len(t) > _MAX_CHARS)
            _t0 = time.monotonic()
            resp = await self._client.post(
                url, headers=headers, json={"model": self._config.model, "input": batch}
            )
            record("embed_http_ms", (time.monotonic() - _t0) * 1000)
            incr("embed_batches")
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            for item in data:
                vec = item["embedding"]
                # dim <= 0 means "unknown" (probe mode: we're calling the embedder precisely
                # to discover its dimension), so skip the guard. Resolved profiles always
                # carry a probed dim > 0, so normal indexing/query still validates.
                if self._config.dim > 0 and len(vec) != self._config.dim:
                    raise ValueError(
                        f"embedder returned dim {len(vec)}, expected {self._config.dim}"
                    )
                vectors.append(vec)
        if truncated:
            logger.warning("embed_truncated", count=truncated, max_chars=_MAX_CHARS)
        return vectors
