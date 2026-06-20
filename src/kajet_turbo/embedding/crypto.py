"""Symmetric encryption for per-user embedding API keys.

Keys are sealed at rest with a Fernet cipher derived from the instance ``SECRET_KEY``
env var, and never returned over the API. Derivation: urlsafe-base64 of
``sha256(SECRET_KEY)`` so any non-empty string is a usable server secret.
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet


class KeyCipher:
    def __init__(self, secret: str):
        if not secret:
            raise ValueError("SECRET_KEY must be set to encrypt embedding API keys")
        derived = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self._fernet = Fernet(derived)

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, token: bytes) -> str:
        return self._fernet.decrypt(token).decode()


def cipher_from_env() -> KeyCipher:
    return KeyCipher(os.getenv("SECRET_KEY", ""))
