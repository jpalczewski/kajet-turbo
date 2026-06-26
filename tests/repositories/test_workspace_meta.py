from kajet_turbo.db import Database
from kajet_turbo.repositories.users import UserRepository
from kajet_turbo.repositories.workspace_meta import WorkspaceMetaRepository


def _user(database: Database) -> str:
    return UserRepository(database.engine).create("a@b.com", "hash")


def test_ensure_creates_default_row(database: Database):
    repo = WorkspaceMetaRepository(database.engine)
    uid = _user(database)
    assert repo.get(uid, "ws") is None
    repo.ensure(uid, "ws")
    assert repo.get(uid, "ws") == {"description": "", "folder": "", "tags": []}


def test_ensure_is_idempotent(database: Database):
    repo = WorkspaceMetaRepository(database.engine)
    uid = _user(database)
    repo.set(uid, "ws", description="keep")
    repo.ensure(uid, "ws")  # must not clobber
    row = repo.get(uid, "ws")
    assert row is not None and row["description"] == "keep"


def test_set_partial_preserves_other_fields(database: Database):
    repo = WorkspaceMetaRepository(database.engine)
    uid = _user(database)
    repo.set(uid, "ws", description="d", folder="Praca", tags='["a"]')
    repo.set(uid, "ws", description="changed")  # folder/tags untouched
    assert repo.get(uid, "ws") == {
        "description": "changed",
        "folder": "Praca",
        "tags": ["a"],
    }


def test_set_can_clear_tags(database: Database):
    repo = WorkspaceMetaRepository(database.engine)
    uid = _user(database)
    repo.set(uid, "ws", tags='["a"]')
    repo.set(uid, "ws", tags="[]")
    row = repo.get(uid, "ws")
    assert row is not None and row["tags"] == []


def test_get_many_returns_only_existing(database: Database):
    repo = WorkspaceMetaRepository(database.engine)
    uid = _user(database)
    repo.set(uid, "alpha", description="A")
    out = repo.get_many(uid, ["alpha", "beta"])
    assert out == {"alpha": {"description": "A", "folder": "", "tags": []}}


def test_settings_default_none(database: Database):
    repo = WorkspaceMetaRepository(database.engine)
    uid = _user(database)
    repo.ensure(uid, "ws")
    assert repo.get_settings(uid, "ws") is None


def test_set_settings_roundtrip_and_preserves_meta(database: Database):
    repo = WorkspaceMetaRepository(database.engine)
    uid = _user(database)
    repo.set(uid, "ws", description="keep")
    repo.set_settings(uid, "ws", '{"validate_links": false}')
    assert repo.get_settings(uid, "ws") == '{"validate_links": false}'
    row = repo.get(uid, "ws")
    assert row is not None and row["description"] == "keep"
    assert row == {"description": "keep", "folder": "", "tags": []}


def test_set_settings_creates_row_when_absent(database: Database):
    repo = WorkspaceMetaRepository(database.engine)
    uid = _user(database)
    repo.set_settings(uid, "ws", '{"validate_links": false}')
    assert repo.get_settings(uid, "ws") == '{"validate_links": false}'
