"""Per-user SSH key management: generate a keypair, seal the private key at rest,
and never hand it back. Mirrors EmbeddingProfileService — the cipher is injected
lazily (it needs SECRET_KEY), and views expose only public material."""

from collections.abc import Callable

from kajet_turbo.crypto import KeyCipher, generate_keypair
from kajet_turbo.crypto.ssh_keys import ALGORITHMS
from kajet_turbo.models import SshKey
from kajet_turbo.repositories.ssh_keys import SshKeyRepository


class SshKeyService:
    def __init__(self, repo: SshKeyRepository, cipher_factory: Callable[[], KeyCipher]):
        self._repo = repo
        self._cipher_factory = cipher_factory

    @staticmethod
    def _view(k: SshKey) -> dict:
        return {
            "id": k.id,
            "name": k.name,
            "algorithm": k.algorithm,
            "fingerprint": k.fingerprint,
            "public_key": k.public_key,
            "created_at": k.created_at,
        }

    def list_keys(self, user_id: str) -> list[dict]:
        return [self._view(k) for k in self._repo.list_for_user(user_id)]

    def create_key(self, user_id: str, name: str, algorithm: str) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("name is required")
        if algorithm not in ALGORITHMS:
            raise ValueError(f"unsupported algorithm: {algorithm!r}")
        keypair = generate_keypair(algorithm)
        sealed = self._cipher_factory().encrypt(keypair.private_key)
        key = self._repo.create(
            user_id, name, algorithm, keypair.public_key, sealed, keypair.fingerprint
        )
        return self._view(key)

    def delete_key(self, user_id: str, key_id: str) -> bool:
        return self._repo.delete(user_id, key_id)
