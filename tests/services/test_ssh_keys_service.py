import pytest
from sqlmodel import Session

from kajet_turbo.crypto import cipher_for
from kajet_turbo.models import User
from kajet_turbo.repositories.ssh_keys import SshKeyRepository
from kajet_turbo.services.ssh_keys import SshKeyService


def _svc(database):
    with Session(database.engine) as s:
        s.add(User(id="u1", email="u@e.com", created_at="2026-01-01"))
        s.commit()
    return SshKeyService(
        SshKeyRepository(database.engine),
        cipher_factory=lambda: cipher_for("ssh-key", secret="server-secret"),
    )


def test_create_key_returns_view_without_private_key(database):
    svc = _svc(database)
    view = svc.create_key("u1", "laptop", "ed25519")
    assert set(view) == {"id", "name", "algorithm", "fingerprint", "public_key", "created_at"}
    assert view["public_key"].startswith("ssh-ed25519 ")
    assert view["fingerprint"].startswith("SHA256:")
    # No private material in the view, under any key.
    assert "PRIVATE" not in repr(view)


def test_create_key_seals_private_key_at_rest(database):
    svc = _svc(database)
    view = svc.create_key("u1", "laptop", "ed25519")
    repo = SshKeyRepository(database.engine)
    row = repo.get("u1", view["id"])
    assert row is not None
    decrypted = cipher_for("ssh-key", secret="server-secret").decrypt(row.private_key_enc)
    assert decrypted.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")


def test_create_key_rejects_unknown_algorithm(database):
    svc = _svc(database)
    with pytest.raises(ValueError):
        svc.create_key("u1", "laptop", "dsa-1024")


def test_create_key_requires_name(database):
    svc = _svc(database)
    with pytest.raises(ValueError):
        svc.create_key("u1", "   ", "ed25519")


def test_list_and_delete(database):
    svc = _svc(database)
    created = svc.create_key("u1", "laptop", "ed25519")
    assert [k["id"] for k in svc.list_keys("u1")] == [created["id"]]
    assert svc.delete_key("u1", created["id"]) is True
    assert svc.list_keys("u1") == []
