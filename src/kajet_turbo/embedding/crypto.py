"""Symmetric encryption for per-user embedding API keys.

Keys are sealed at rest with a Fernet cipher whose key is stretched from the instance
``SECRET_KEY`` env var with scrypt (fixed application salt). scrypt is used instead of a
bare hash so that even a low-entropy ``SECRET_KEY`` is not cheaply brute-forceable. The
derivation is memoized, so its cost (~40 ms) is paid once per distinct secret per process.
"""

import base64
import functools
import hashlib
import os

from cryptography.fernet import Fernet

# scrypt needs a salt, but per-deployment uniqueness already comes from SECRET_KEY
# itself, so a constant app-scoped salt is sufficient here.
_SALT = b"kajet-turbo/embedding-key/v1"
_SCRYPT_N = 2**14  # ~16 MB / ~40 ms, within OpenSSL's default maxmem


@functools.lru_cache(maxsize=8)
def _derive_key(secret: str) -> bytes:
    raw = hashlib.scrypt(secret.encode(), salt=_SALT, n=_SCRYPT_N, r=8, p=1, dklen=32)
    return base64.urlsafe_b64encode(raw)


class KeyCipher:
    def __init__(self, secret: str):
        if not secret:
            raise ValueError("SECRET_KEY must be set to encrypt embedding API keys")
        self._fernet = Fernet(_derive_key(secret))

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, token: bytes) -> str:
        return self._fernet.decrypt(token).decode()


def cipher_from_env() -> KeyCipher:
    return KeyCipher(os.getenv("SECRET_KEY", ""))
