"""Symmetric sealing of secrets at rest.

Each purpose seals with a Fernet cipher whose key is stretched from the instance
``SECRET_KEY`` via scrypt with the purpose's fixed salt. scrypt (not a bare hash)
keeps even a low-entropy ``SECRET_KEY`` from being cheaply brute-forced. The
derivation is memoized, so its cost (~40 ms) is paid once per (secret, salt) per
process.
"""

import base64
import functools
import hashlib
import os

from cryptography.fernet import Fernet

from kajet_turbo.crypto.purposes import EMBEDDING, salt_for

_SCRYPT_N = 2**14  # ~16 MB / ~40 ms, within OpenSSL's default maxmem


@functools.lru_cache(maxsize=16)
def _derive_key(secret: str, salt: bytes) -> bytes:
    raw = hashlib.scrypt(secret.encode(), salt=salt, n=_SCRYPT_N, r=8, p=1, dklen=32)
    return base64.urlsafe_b64encode(raw)


class KeyCipher:
    def __init__(self, secret: str, salt: bytes):
        if not secret:
            raise ValueError("SECRET_KEY must be set to seal secrets at rest")
        self._fernet = Fernet(_derive_key(secret, salt))

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, token: bytes) -> str:
        return self._fernet.decrypt(token).decode()


def cipher_for(purpose: str, secret: str | None = None) -> KeyCipher:
    if secret is None:
        secret = os.getenv("SECRET_KEY", "")
    return KeyCipher(secret, salt_for(purpose))


def cipher_from_env() -> KeyCipher:
    """Embedding-purpose cipher from the ``SECRET_KEY`` env var (back-compat name)."""
    return cipher_for(EMBEDDING)
