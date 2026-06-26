"""Crypto purposes and their fixed salts.

Each purpose derives a separate Fernet key from the same ``SECRET_KEY`` by using
a distinct, stable salt. Separation means a key sealed for one purpose cannot be
decrypted by another. Salts are application-scoped constants (per-deployment
uniqueness already comes from ``SECRET_KEY``), so they must never change once data
exists — that would orphan all existing ciphertext for that purpose.
"""

EMBEDDING = "embedding"
SSH_KEY = "ssh-key"

_SALTS: dict[str, bytes] = {
    EMBEDDING: b"kajet-turbo/embedding-key/v1",
    SSH_KEY: b"kajet-turbo/ssh-key/v1",
}


def salt_for(purpose: str) -> bytes:
    try:
        return _SALTS[purpose]
    except KeyError:
        raise ValueError(f"Unknown crypto purpose: {purpose!r}") from None
