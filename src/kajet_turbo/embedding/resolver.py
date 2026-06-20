"""resolve_backend(user_id): the single place that decides which embedding backend a
user gets. Picks the user's backend_id (or the instance default) from the registry and
binds the resolved API key (per-user sealed key → instance fallback → none)."""

import os

from sqlalchemy import Engine

from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.crypto import KeyCipher, cipher_from_env
from kajet_turbo.embedding.registry import Registry, load_registry
from kajet_turbo.repositories.embedding_config import EmbeddingConfigRepository


class BackendResolver:
    def __init__(
        self,
        registry: Registry,
        config_repo: EmbeddingConfigRepository,
        cipher: KeyCipher,
        *,
        instance_fallback_key: str | None = None,
    ):
        self._registry = registry
        self._config_repo = config_repo
        self._cipher = cipher
        self._fallback_key = instance_fallback_key

    def resolve_backend(self, user_id: str) -> EmbedderConfig | None:
        user_cfg = self._config_repo.get(user_id)
        backend_id = user_cfg.backend_id if user_cfg else None
        definition = self._registry.get(backend_id)
        if definition is None:
            return None
        api_key: str | None = None
        if user_cfg and user_cfg.api_key_enc:
            api_key = self._cipher.decrypt(user_cfg.api_key_enc)
        if api_key is None:
            api_key = self._fallback_key
        return definition.to_config(api_key)


def resolver_from_env(engine: Engine) -> BackendResolver:
    return BackendResolver(
        registry=load_registry(),
        config_repo=EmbeddingConfigRepository(engine),
        cipher=cipher_from_env(),
        instance_fallback_key=os.getenv("EMBEDDING_API_KEY"),
    )
