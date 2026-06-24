import pytest

from kajet_turbo.db import Database
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.users import UserRepository
from kajet_turbo.repositories.workspace_meta import WorkspaceMetaRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.services.workspaces import WorkspaceService


def _service(database: Database) -> WorkspaceService:
    return WorkspaceService(
        WorkspaceRepository(database.engine),
        NoteRepository(database.engine),
        WorkspaceMetaRepository(database.engine),
    )


def _user(database: Database) -> str:
    return UserRepository(database.engine).create("a@b.com", "hash")


def test_set_meta_normalizes_folder_and_tags(database: Database):
    svc = _service(database)
    uid = _user(database)
    svc._repo.grant_access(uid, "ws")
    out = svc.set_meta(uid, "ws", description="docs", folder="/Praca/Klienci/", tags=["#Work", "x"])
    assert out == {"description": "docs", "folder": "Praca/Klienci", "tags": ["work", "x"]}


def test_set_meta_partial_update(database: Database):
    svc = _service(database)
    uid = _user(database)
    svc._repo.grant_access(uid, "ws")
    svc.set_meta(uid, "ws", description="d", folder="A")
    out = svc.set_meta(uid, "ws", tags=["t"])
    assert out == {"description": "d", "folder": "A", "tags": ["t"]}


def test_list_meta_includes_defaults_for_missing_rows(database: Database):
    svc = _service(database)
    uid = _user(database)
    svc._repo.grant_access(uid, "alpha")
    svc._repo.grant_access(uid, "beta")
    svc.set_meta(uid, "alpha", description="A")
    out = {r["name"]: r for r in svc.list_meta(uid)}
    assert out["alpha"]["description"] == "A"
    assert out["beta"] == {"name": "beta", "description": "", "folder": "", "tags": []}


def test_set_meta_rejects_invalid_tag(database: Database):
    svc = _service(database)
    uid = _user(database)
    svc._repo.grant_access(uid, "ws")
    with pytest.raises(ValueError):
        svc.set_meta(uid, "ws", tags=["bad tag"])  # space -> normalize returns None
