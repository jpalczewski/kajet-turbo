"""Per-user embedding backend selection + key management over the instance registry.

The API key is write-only: stored encrypted, never returned (only ``has_key``). ``set_config``
is a partial update — an omitted/empty key keeps the existing one."""

from collections.abc import Callable

from kajet_turbo.embedding.crypto import KeyCipher
from kajet_turbo.embedding.registry import Registry
from kajet_turbo.repositories.embedding_config import EmbeddingConfigRepository


class EmbeddingConfigService:
    def __init__(
        self,
        registry: Registry,
        config_repo: EmbeddingConfigRepository,
        cipher_factory: Callable[[], KeyCipher],
    ):
        self._registry = registry
        self._config_repo = config_repo
        self._cipher_factory = cipher_factory

    def list_backends(self, user_id: str) -> dict:
        row = self._config_repo.get(user_id)
        return {
            "backends": [
                {
                    "backend_id": b.backend_id,
                    "type": b.type,
                    "model": b.model,
                    "dim": b.dim,
                    "base_url": b.base_url,
                }
                for b in self._registry.backends.values()
            ],
            "default_id": self._registry.default_id,
            "selected": row.backend_id if row else None,
            "has_key": bool(row and row.api_key_enc),
        }

    def set_config(self, user_id: str, backend_id: str | None, api_key: str | None) -> dict:
        """Partial update. ``backend_id`` (if given) must be a known backend. ``api_key``:
        non-empty → encrypt + store; None/empty → keep the existing key."""
        if backend_id is not None and backend_id not in self._registry.backends:
            raise ValueError(f"unknown backend_id: {backend_id!r}")
        existing = self._config_repo.get(user_id)
        new_backend = (
            backend_id if backend_id is not None else (existing.backend_id if existing else None)
        )
        if api_key:
            new_key_enc = self._cipher_factory().encrypt(api_key)
        else:
            new_key_enc = existing.api_key_enc if existing else None
        self._config_repo.upsert(user_id, new_backend, new_key_enc)
        return {"backend_id": new_backend, "has_key": bool(new_key_enc)}
