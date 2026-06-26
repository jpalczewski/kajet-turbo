import pytest

from kajet_turbo.crypto.cipher import cipher_for
from kajet_turbo.crypto.purposes import EMBEDDING, SSH_KEY


def test_encrypt_decrypt_round_trip():
    cipher = cipher_for(EMBEDDING, secret="server-secret")
    token = cipher.encrypt("sk-user-key-123")
    assert isinstance(token, bytes)
    assert token != b"sk-user-key-123"
    assert cipher.decrypt(token) == "sk-user-key-123"


def test_same_plaintext_yields_different_ciphertext_but_decrypts():
    cipher = cipher_for(EMBEDDING, secret="server-secret")
    a = cipher.encrypt("same")
    b = cipher.encrypt("same")
    assert a != b  # Fernet embeds a random IV + timestamp
    assert cipher.decrypt(a) == cipher.decrypt(b) == "same"


def test_different_secret_cannot_decrypt():
    from cryptography.fernet import InvalidToken

    token = cipher_for(EMBEDDING, secret="secret-a").encrypt("payload")
    with pytest.raises(InvalidToken):
        cipher_for(EMBEDDING, secret="secret-b").decrypt(token)


def test_different_purpose_cannot_decrypt():
    # Purpose separation: same secret, different salt -> different key.
    from cryptography.fernet import InvalidToken

    token = cipher_for(EMBEDDING, secret="server-secret").encrypt("payload")
    with pytest.raises(InvalidToken):
        cipher_for(SSH_KEY, secret="server-secret").decrypt(token)


def test_empty_secret_raises():
    with pytest.raises(ValueError):
        cipher_for(EMBEDDING, secret="")


def test_key_is_stable_across_instances():
    # Tokens must survive process restarts: the same secret + purpose must always
    # derive the same key (fixed salt). A random salt would break this.
    token = cipher_for(EMBEDDING, secret="server-secret").encrypt("persisted")
    assert cipher_for(EMBEDDING, secret="server-secret").decrypt(token) == "persisted"


def test_cipher_from_env_uses_embedding_purpose(monkeypatch):
    from kajet_turbo.crypto.cipher import cipher_from_env

    monkeypatch.setenv("SECRET_KEY", "server-secret")
    token = cipher_from_env().encrypt("x")
    assert cipher_for(EMBEDDING, secret="server-secret").decrypt(token) == "x"
