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


@pytest.fixture
def workspace_service(database: Database) -> WorkspaceService:
    return WorkspaceService(
        WorkspaceRepository(database.engine),
        NoteRepository(database.engine),
        WorkspaceMetaRepository(database.engine),
    )


@pytest.fixture
def uid(database: Database) -> str:
    return UserRepository(database.engine).create("a@b.com", "hash")


def test_set_meta_normalizes_folder_and_tags(database: Database):
    svc = _service(database)
    uid = _user(database)
    svc._repo.grant_access(uid, "ws")
    out = svc.set_meta(uid, "ws", description="docs", folder="/Praca/Klienci/", tags=["#Work", "x"])
    assert out == {
        "description": "docs",
        "folder": "Praca/Klienci",
        "tags": ["work", "x"],
    }


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


def test_get_settings_returns_defaults_when_unset(workspace_service: WorkspaceService, uid: str):
    workspace_service._repo.grant_access(uid, "ws")
    workspace_service._meta_repo.ensure(uid, "ws")
    assert workspace_service.get_settings(uid, "ws") == {"validate_links": True}


def test_set_setting_persists_and_returns_full_dict(workspace_service: WorkspaceService, uid: str):
    workspace_service._repo.grant_access(uid, "ws")
    workspace_service._meta_repo.ensure(uid, "ws")
    out = workspace_service.set_setting(uid, "ws", "validate_links", False)
    assert out == {"validate_links": False}
    assert workspace_service.get_settings(uid, "ws") == {"validate_links": False}


def test_set_setting_rejects_unknown_key(workspace_service: WorkspaceService, uid: str):
    workspace_service._repo.grant_access(uid, "ws")
    workspace_service._meta_repo.ensure(uid, "ws")
    with pytest.raises(ValueError):
        workspace_service.set_setting(uid, "ws", "ghost", True)


def test_set_setting_rejects_wrong_type(workspace_service: WorkspaceService, uid: str):
    workspace_service._repo.grant_access(uid, "ws")
    workspace_service._meta_repo.ensure(uid, "ws")
    with pytest.raises(ValueError):
        workspace_service.set_setting(uid, "ws", "validate_links", "yes")
