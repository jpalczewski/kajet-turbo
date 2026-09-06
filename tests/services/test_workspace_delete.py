from pathlib import Path

import pytest
from sqlmodel import Session

from kajet_turbo.models import Job
from kajet_turbo.repositories.jobs import JobRepository
from tests.services.conftest import build_workspace_service, seed_user
from tests.services.helpers import seed_full_workspace, workspace_table_counts


def test_delete_wipes_every_table_and_the_directory(
    database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_workspace_factory
):
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path))
    ws_dir = git_workspace_factory("u1/ws")
    seed_full_workspace(database, user_id="u1", name="ws")
    global_job_id = JobRepository(database.engine).enqueue(
        "sweep_outbox", {}, dedup_key="sweep_outbox"
    )
    svc = build_workspace_service(database)
    svc._repo.grant_access("u1", "ws")
    svc._meta_repo.ensure("u1", "ws")

    before = workspace_table_counts(database, workspace="ws", owner_id="u1")
    assert all(v > 0 for v in before.values()), before

    svc.delete("u1", "ws")

    after = workspace_table_counts(database, workspace="ws", owner_id="u1")
    assert all(v == 0 for v in after.values()), after
    assert not ws_dir.exists()
    assert svc.has_access("u1", "ws") is False
    # global (user_id=None, no payload.workspace) jobs must not be swept up
    with Session(database.engine) as session:
        assert session.get(Job, global_job_id) is not None


def test_delete_is_owner_scoped(
    database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_workspace_factory
):
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path))
    u1_dir = git_workspace_factory("u1/ws")
    u2_dir = git_workspace_factory("u2/ws")
    seed_full_workspace(database, user_id="u1", name="ws")
    seed_full_workspace(database, user_id="u2", name="ws")
    svc = build_workspace_service(database)
    for uid in ("u1", "u2"):
        svc._repo.grant_access(uid, "ws")
        svc._meta_repo.ensure(uid, "ws")

    svc.delete("u1", "ws")

    assert not u1_dir.exists()
    assert u2_dir.exists()
    assert svc.has_access("u1", "ws") is False
    assert svc.has_access("u2", "ws") is True
    other = workspace_table_counts(database, workspace="ws", owner_id="u2")
    assert all(v > 0 for v in other.values()), other


def test_delete_is_idempotent(
    database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_workspace_factory
):
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path))
    seed_user(database, "u1")
    git_workspace_factory("u1/ws")
    svc = build_workspace_service(database)
    svc._repo.grant_access("u1", "ws")
    svc._meta_repo.ensure("u1", "ws")

    svc.delete("u1", "ws")
    svc.delete("u1", "ws")  # must not raise on a second call


def test_delete_nonexistent_workspace_is_a_noop(
    database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path))
    seed_user(database, "u1")
    svc = build_workspace_service(database)

    svc.delete("u1", "never-existed")  # must not raise
