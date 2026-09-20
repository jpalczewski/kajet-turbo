"""Embedder port: the contract every backend adapter implements, plus the resolved
backend config that binds a registry definition to a user's API key."""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class EmbeddingAuthError(Exception):
    """The embedding backend rejected our credentials (HTTP 401/403).

    A configuration problem — a rotated or revoked API key — so retrying the same
    request cannot succeed. Distinct from transient failures (timeouts, 5xx, 429)
    so callers can fail fast and log it loudly instead of degrading silently.
    """

    def __init__(self, backend: str, status_code: int):
        super().__init__(
            f"Embedding backend {backend} rejected the API key (HTTP {status_code}); "
            "check the key configured for the active embedding profile"
        )
        self.backend = backend
        self.status_code = status_code


class EmbeddingRequestRejected(Exception):
    """The embedding backend understood the credentials but refused the request itself
    (HTTP 400/404/422) — typically an unknown model or a wrong base URL. ``detail`` is
    the provider's own explanation, with the API key redacted, for operator logs."""

    def __init__(self, backend: str, status_code: int, detail: str):
        super().__init__(f"Embedding backend {backend} rejected the request (HTTP {status_code})")
        self.backend = backend
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class EmbedderConfig:
    backend_id: str
    type: str  # 'openai' | 'hf' | ...
    model: str
    dim: int
    base_url: str
    query_prefix: str = ""
    passage_prefix: str = ""
    # resolved per-user (or instance fallback); None → cannot embed.
    # repr=False so a plaintext key never leaks via logs or tracebacks.
    api_key: str | None = field(default=None, repr=False)


@runtime_checkable
class Embedder(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def dim(self) -> int: ...
    @property
    def query_prefix(self) -> str: ...
    @property
    def passage_prefix(self) -> str: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...
