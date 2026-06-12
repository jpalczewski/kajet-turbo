from kajet_turbo.auth import hash_password, verify_password


def test_hash_and_verify_password():
    hashed = hash_password("secret123")

    assert verify_password(hashed, "secret123") is True
    assert verify_password(hashed, "wrong") is False


def test_verify_password_rejects_empty_hash():
    assert verify_password("", "anything") is False
