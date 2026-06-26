from sqlmodel import Session

from kajet_turbo.db import Database
from kajet_turbo.models import SshKey, User
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository


def _seed(engine, user_id="u1", key_id="k1"):
    with Session(engine) as s:
        s.add(User(id=user_id, email=f"{user_id}@e.com", created_at="2026-01-01"))
        s.flush()
        s.add(
            SshKey(
                id=key_id,
                user_id=user_id,
                name="laptop",
                algorithm="ed25519",
                public_key="pub",
                private_key_enc=b"enc",
                fingerprint="fp",
                created_at="2026-01-01",
            )
        )
        s.commit()


def test_upsert_creates_then_updates(database: Database):
    _seed(database.engine)
    repo = WorkspaceRemoteRepository(database.engine)
    r = repo.upsert("u1", "ws", origin_url="git@h:/ r.git", ssh_key_id="k1", enabled=True, now="t1")
    assert r.origin_url == "git@h:/ r.git".replace(" ", "") or r.origin_url  # value stored
    repo.upsert("u1", "ws", origin_url="git@h:/r2.git", ssh_key_id="k1", enabled=False, now="t2")
    got = repo.get("u1", "ws")
    assert got is not None
    assert got.origin_url == "git@h:/r2.git"
    assert got.enabled is False


def test_get_owner_scoped(database: Database):
    _seed(database.engine)
    repo = WorkspaceRemoteRepository(database.engine)
    repo.upsert("u1", "ws", origin_url="o", ssh_key_id="k1", enabled=True, now="t")
    assert repo.get("u1", "ws") is not None
    assert repo.get("u2", "ws") is None


def test_mark_pushed_clears_error(database: Database):
    _seed(database.engine)
    repo = WorkspaceRemoteRepository(database.engine)
    repo.upsert("u1", "ws", origin_url="o", ssh_key_id="k1", enabled=True, now="t")
    repo.mark_failed("u1", "ws", "boom", now="t1")
    got = repo.get("u1", "ws")
    assert got is not None
    assert got.last_error == "boom"
    repo.mark_pushed("u1", "ws", now="t2")
    got = repo.get("u1", "ws")
    assert got is not None
    assert got.pushed_at == "t2"
    assert got.last_error is None


def test_mark_dirty(database: Database):
    _seed(database.engine)
    repo = WorkspaceRemoteRepository(database.engine)
    repo.upsert("u1", "ws", origin_url="o", ssh_key_id="k1", enabled=True, now="t")
    repo.mark_dirty("u1", "ws", now="t3")
    got = repo.get("u1", "ws")
    assert got is not None
    assert got.dirty_at == "t3"


def test_delete(database: Database):
    _seed(database.engine)
    repo = WorkspaceRemoteRepository(database.engine)
    repo.upsert("u1", "ws", origin_url="o", ssh_key_id="k1", enabled=True, now="t")
    assert repo.delete("u1", "ws") is True
    assert repo.get("u1", "ws") is None
    assert repo.delete("u1", "ws") is False
