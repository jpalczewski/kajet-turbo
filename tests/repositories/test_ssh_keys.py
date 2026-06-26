import pytest
from sqlmodel import Session

from kajet_turbo.db import Database
from kajet_turbo.models import User
from kajet_turbo.repositories.ssh_keys import DuplicateKeyName, SshKeyRepository


def _user(engine, user_id: str) -> None:
    with Session(engine) as session:
        session.add(User(id=user_id, email=f"{user_id}@e.com", created_at="2026-01-01"))
        session.commit()


def test_create_and_list_owner_scoped(database: Database):
    _user(database.engine, "u1")
    _user(database.engine, "u2")
    repo = SshKeyRepository(database.engine)
    repo.create("u1", "laptop", "ed25519", "ssh-ed25519 AAAA u1", b"enc1", "SHA256:aaa")
    repo.create("u2", "laptop", "ed25519", "ssh-ed25519 AAAA u2", b"enc2", "SHA256:bbb")
    keys = repo.list_for_user("u1")
    assert [k.name for k in keys] == ["laptop"]
    assert keys[0].public_key == "ssh-ed25519 AAAA u1"


def test_create_duplicate_name_raises(database: Database):
    _user(database.engine, "u1")
    repo = SshKeyRepository(database.engine)
    repo.create("u1", "laptop", "ed25519", "pub", b"enc", "fp")
    with pytest.raises(DuplicateKeyName):
        repo.create("u1", "laptop", "rsa-4096", "pub2", b"enc2", "fp2")


def test_same_name_different_users_ok(database: Database):
    _user(database.engine, "u1")
    _user(database.engine, "u2")
    repo = SshKeyRepository(database.engine)
    repo.create("u1", "laptop", "ed25519", "p1", b"e1", "f1")
    repo.create("u2", "laptop", "ed25519", "p2", b"e2", "f2")  # no raise


def test_get_owner_scoped(database: Database):
    _user(database.engine, "u1")
    _user(database.engine, "u2")
    repo = SshKeyRepository(database.engine)
    key = repo.create("u1", "laptop", "ed25519", "p", b"e", "f")
    assert repo.get("u1", key.id) is not None
    assert repo.get("u2", key.id) is None  # not owner


def test_delete_owner_scoped(database: Database):
    _user(database.engine, "u1")
    _user(database.engine, "u2")
    repo = SshKeyRepository(database.engine)
    key = repo.create("u1", "laptop", "ed25519", "p", b"e", "f")
    assert repo.delete("u2", key.id) is False  # not owner
    assert repo.delete("u1", key.id) is True
    assert repo.get("u1", key.id) is None
    assert repo.delete("u1", key.id) is False  # already gone
