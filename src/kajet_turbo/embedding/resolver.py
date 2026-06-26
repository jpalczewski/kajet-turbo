"""resolve_backend(user_id): resolves the user's ACTIVE embedding profile into an
EmbedderConfig. The cache/index identity (backend_id) is the profile's base_url, so two
profiles pointing at the same endpoint+model reuse cached embeddings. No active profile
(or no profile) → None → callers degrade to FTS-only."""

from collections.abc import Callable

from kajet_turbo.crypto import KeyCipher
from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.repositories.embedding_profiles import EmbeddingProfileRepository


class ProfileResolver:
    def __init__(self, repo: EmbeddingProfileRepository, cipher_factory: Callable[[], KeyCipher]):
        self._repo = repo
        self._cipher_factory = cipher_factory  # lazy: SECRET_KEY only needed to decrypt a key

    def resolve_backend(self, user_id: str) -> EmbedderConfig | None:
        p = self._repo.get_active(user_id)
        if p is None:
            return None
        api_key = self._cipher_factory().decrypt(p.api_key_enc) if p.api_key_enc else None
        return EmbedderConfig(
            backend_id=p.base_url,  # cache/index identity = endpoint
            type="openai",
            model=p.model,
            dim=p.dim,
            base_url=p.base_url,
            api_key=api_key,
        )
