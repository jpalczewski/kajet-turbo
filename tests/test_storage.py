import pytest
from kajet_turbo.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    yield s
    s.close()


def test_schema_creates_all_tables(storage):
    conn = storage._conn
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "notes" in names
    assert "oauth_clients" in names
    assert "users" in names
    assert "workspace_access" in names


def test_schema_creates_virtual_tables(storage):
    conn = storage._conn
    # FTS5 creates tables with suffixes _data, _idx etc.
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master"
    ).fetchall()}
    assert any(n.startswith("notes_fts") for n in names)
    assert any(n.startswith("notes_vec") for n in names)


def test_vec_version_loaded(storage):
    version = storage._conn.execute("SELECT vec_version()").fetchone()[0]
    assert version.startswith("v")


def test_wal_mode_enabled(storage):
    mode = storage._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_insert_and_get_note(storage):
    storage.insert_note("abc1234", "ws1", "Moja notatka", ["python"], _now(), _now(), "treść notatki")
    note = storage.get_note("abc1234")
    assert note["id"] == "abc1234"
    assert note["title"] == "Moja notatka"
    assert note["workspace"] == "ws1"


def test_get_note_returns_none_for_missing(storage):
    assert storage.get_note("nieistnieje") is None


def test_update_note(storage):
    storage.insert_note("abc1234", "ws1", "Stary tytuł", [], _now(), _now(), "treść")
    storage.update_note("abc1234", title="Nowy tytuł", content="nowa treść", updated_at=_now())
    note = storage.get_note("abc1234")
    assert note["title"] == "Nowy tytuł"


def test_delete_note(storage):
    storage.insert_note("abc1234", "ws1", "Do usunięcia", [], _now(), _now(), "treść")
    storage.delete_note("abc1234")
    assert storage.get_note("abc1234") is None


def test_list_notes_by_workspace(storage):
    storage.insert_note("id1", "ws1", "Notatka 1", ["a"], _now(), _now(), "treść 1")
    storage.insert_note("id2", "ws1", "Notatka 2", ["b"], _now(), _now(), "treść 2")
    storage.insert_note("id3", "ws2", "Notatka 3", [], _now(), _now(), "treść 3")
    notes = storage.list_notes("ws1")
    ids = [n["id"] for n in notes]
    assert "id1" in ids
    assert "id2" in ids
    assert "id3" not in ids


def test_list_notes_filter_by_tag(storage):
    storage.insert_note("id1", "ws1", "Tagged", ["python", "mcp"], _now(), _now(), "treść")
    storage.insert_note("id2", "ws1", "Untagged", [], _now(), _now(), "treść")
    notes = storage.list_notes("ws1", tags=["python"])
    ids = [n["id"] for n in notes]
    assert "id1" in ids
    assert "id2" not in ids
