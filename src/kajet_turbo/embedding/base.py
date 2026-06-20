"""Embedder port: the contract every backend adapter implements, plus the resolved
backend config that binds a registry definition to a user's API key."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EmbedderConfig:
    backend_id: str
    type: str  # 'openai' | 'hf' | ...
    model: str
    dim: int
    base_url: str
    query_prefix: str = ""
    passage_prefix: str = ""
    api_key: str | None = None  # resolved per-user (or instance fallback); None → cannot embed


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
