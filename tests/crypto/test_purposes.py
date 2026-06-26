import pytest

from kajet_turbo.crypto.purposes import EMBEDDING, SSH_KEY, salt_for


def test_known_purposes_have_distinct_stable_salts():
    assert salt_for(EMBEDDING) == b"kajet-turbo/embedding-key/v1"
    assert salt_for(SSH_KEY) == b"kajet-turbo/ssh-key/v1"
    assert salt_for(EMBEDDING) != salt_for(SSH_KEY)


def test_unknown_purpose_raises():
    with pytest.raises(ValueError):
        salt_for("does-not-exist")
