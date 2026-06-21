"""Per-user embedding profile management: CRUD + activate, key encryption, and a probe
embed that validates the profile and captures its vector ``dim`` at save time. Keys are
write-only (stored sealed, never returned — only ``has_key``)."""

from collections.abc import Callable

from kajet_turbo.embedding.crypto import KeyCipher
from kajet_turbo.repositories.embedding_profiles import EmbeddingProfileRepository

# probe_dim(base_url, model, api_key) -> int (vector length); raises on connection/auth error.
ProbeDim = Callable[[str, str, str | None], int]


class EmbeddingProfileService:
    def __init__(
        self,
        repo: EmbeddingProfileRepository,
        cipher_factory: Callable[[], KeyCipher],
        probe_dim: ProbeDim,
    ):
        self._repo = repo
        self._cipher_factory = cipher_factory  # lazy: SECRET_KEY only needed when sealing a key
        self._probe = probe_dim

    @staticmethod
    def _view(p) -> dict:
        return {
            "id": p.id,
            "name": p.name,
            "base_url": p.base_url,
            "model": p.model,
            "dim": p.dim,
            "is_active": p.is_active,
            "has_key": bool(p.api_key_enc),
        }

    def list_profiles(self, user_id: str) -> list[dict]:
        return [self._view(p) for p in self._repo.list_for_user(user_id)]

    def create_profile(
        self,
        user_id: str,
        name: str,
        base_url: str,
        model: str,
        api_key: str | None,
    ) -> dict:
        dim = self._probe_dim(base_url, model, api_key)
        key_enc = self._cipher_factory().encrypt(api_key) if api_key else None
        p = self._repo.create(user_id, name, base_url, model, key_enc, dim)
        return self._view(p)

    def update_profile(
        self,
        user_id: str,
        profile_id: str,
        *,
        name: str,
        base_url: str,
        model: str,
        api_key: str | None,
    ) -> dict:
        existing = self._repo.get(user_id, profile_id)
        if existing is None:
            raise ValueError("profile not found")
        dim = self._probe_dim(base_url, model, api_key)
        # non-empty key → reseal; omitted → keep the existing sealed key
        key_enc = self._cipher_factory().encrypt(api_key) if api_key else existing.api_key_enc
        self._repo.update(
            user_id,
            profile_id,
            name=name,
            base_url=base_url,
            model=model,
            api_key_enc=key_enc,
            dim=dim,
        )
        return self._view(self._repo.get(user_id, profile_id))

    def activate_profile(self, user_id: str, profile_id: str) -> None:
        self._repo.set_active(user_id, profile_id)

    def delete_profile(self, user_id: str, profile_id: str) -> None:
        self._repo.delete(user_id, profile_id)

    def _probe_dim(self, base_url: str, model: str, api_key: str | None) -> int:
        try:
            dim = self._probe(base_url, model, api_key)
        except Exception as e:
            raise ValueError(f"Nie udało się połączyć z embedderem: {e}") from e
        if not isinstance(dim, int) or dim <= 0:
            raise ValueError("Embedder zwrócił niepoprawny wektor.")
        return dim
