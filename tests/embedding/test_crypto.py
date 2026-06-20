import pytest

from kajet_turbo.embedding.crypto import KeyCipher


def test_encrypt_decrypt_round_trip():
    cipher = KeyCipher("server-secret")
    token = cipher.encrypt("sk-user-key-123")
    assert isinstance(token, bytes)
    assert token != b"sk-user-key-123"
    assert cipher.decrypt(token) == "sk-user-key-123"


def test_same_plaintext_yields_different_ciphertext_but_decrypts():
    cipher = KeyCipher("server-secret")
    a = cipher.encrypt("same")
    b = cipher.encrypt("same")
    assert a != b  # Fernet embeds a random IV + timestamp
    assert cipher.decrypt(a) == cipher.decrypt(b) == "same"


def test_different_secret_cannot_decrypt():
    from cryptography.fernet import InvalidToken

    token = KeyCipher("secret-a").encrypt("payload")
    with pytest.raises(InvalidToken):
        KeyCipher("secret-b").decrypt(token)


def test_empty_secret_raises():
    with pytest.raises(ValueError):
        KeyCipher("")
