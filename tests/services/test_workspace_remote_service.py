import pytest
from sqlmodel import Session

from kajet_turbo.models import SshKey, User
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.ssh_keys import SshKeyRepository
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository
from kajet_turbo.services.workspace_remote import WorkspaceRemoteService


def _svc(database, tmp_path):
    with Session(database.engine) as s:
        s.add(User(id="u1", email="u@e.com", created_at="2026-01-01"))
        s.flush()
        s.add(
            SshKey(
                id="k1",
                user_id="u1",
                name="laptop",
                algorithm="ed25519",
                public_key="p",
                private_key_enc=b"e",
                fingerprint="f",
                created_at="2026-01-01",
            )
        )
        s.commit()
    return WorkspaceRemoteService(
        WorkspaceRemoteRepository(database.engine),
        SshKeyRepository(database.engine),
        JobRepository(database.engine),
        workspaces_dir=str(tmp_path),
    )


def test_set_then_get(database, tmp_path):
    svc = _svc(database, tmp_path)
    assert svc.get("u1", "ws") is None
    view = svc.set("u1", "ws", origin_url="git@h:/r.git", ssh_key_id="k1", enabled=True)
    assert view["origin_url"] == "git@h:/r.git"
    assert view["ssh_key_id"] == "k1"
    assert view["enabled"] is True
    got = svc.get("u1", "ws")
    assert got["origin_url"] == "git@h:/r.git"


def test_set_rejects_other_users_key(database, tmp_path):
    svc = _svc(database, tmp_path)
    with pytest.raises(ValueError):
        svc.set("u1", "ws", origin_url="o", ssh_key_id="does-not-exist", enabled=True)


def test_set_rejects_empty_origin(database, tmp_path):
    svc = _svc(database, tmp_path)
    with pytest.raises(ValueError):
        svc.set("u1", "ws", origin_url="  ", ssh_key_id="k1", enabled=True)


def test_delete(database, tmp_path):
    svc = _svc(database, tmp_path)
    svc.set("u1", "ws", origin_url="o", ssh_key_id="k1", enabled=True)
    assert svc.delete("u1", "ws") is True
    assert svc.get("u1", "ws") is None


def test_trigger_push_enqueues_for_enabled(database, tmp_path):
    svc = _svc(database, tmp_path)
    svc.set("u1", "ws", origin_url="o", ssh_key_id="k1", enabled=True)
    assert svc.trigger_push("u1", "ws") is True
    jobs = JobRepository(database.engine).list_jobs("u1")
    assert [j.kind for j in jobs] == ["push_workspace"]


def test_trigger_push_rejects_disabled_or_missing(database, tmp_path):
    svc = _svc(database, tmp_path)
    assert svc.trigger_push("u1", "ws") is False  # no remote
    svc.set("u1", "ws", origin_url="o", ssh_key_id="k1", enabled=False)
    assert svc.trigger_push("u1", "ws") is False  # disabled
    assert JobRepository(database.engine).list_jobs("u1") == []
