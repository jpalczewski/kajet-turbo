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
