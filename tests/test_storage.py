import struct

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


def test_fts_search_finds_by_title(storage):
    storage.insert_note("id1", "ws1", "Python async programming", ["python"], _now(), _now(), "tutorial o asyncio")
    storage.insert_note("id2", "ws1", "JavaScript basics", ["js"], _now(), _now(), "podstawy JS")
    results = storage.search_fts("async", "ws1")
    ids = [r["id"] for r in results]
    assert "id1" in ids
    assert "id2" not in ids


def test_fts_search_finds_by_content(storage):
    storage.insert_note("id1", "ws1", "Notatka", [], _now(), _now(), "sqlite jest świetny do embeddingów")
    results = storage.search_fts("embedding", "ws1")
    assert any(r["id"] == "id1" for r in results)


def test_fts_search_respects_workspace(storage):
    storage.insert_note("id1", "ws1", "Python notatka", [], _now(), _now(), "treść")
    storage.insert_note("id2", "ws2", "Python inny workspace", [], _now(), _now(), "treść")
    results = storage.search_fts("Python", "ws1")
    ids = [r["id"] for r in results]
    assert "id1" in ids
    assert "id2" not in ids


def test_fts_search_trigram_partial(storage):
    storage.insert_note("id1", "ws1", "Programowanie", [], _now(), _now(), "nauka programowania w Pythonie")
    # trigram tokenizer: "gram" should match "programowanie"
    results = storage.search_fts("gram", "ws1")
    assert any(r["id"] == "id1" for r in results)


def test_update_note_title_only_preserves_fts_content(storage):
    storage.insert_note("abc1234", "ws1", "Stary tytuł", [], _now(), _now(), "unikalna treść notatki")
    storage.update_note("abc1234", title="Nowy tytuł", updated_at=_now())
    results = storage.search_fts("unikalna", "ws1")
    assert any(r["id"] == "abc1234" for r in results)


def _fake_embedding(val: float, dim: int = 4) -> bytes:
    """Small test vector (not 1536-dimensional)."""
    return struct.pack(f"{dim}f", *[val] * dim)


@pytest.fixture
def storage_small_dim(tmp_path):
    """Storage with small EMBEDDING_DIM=4 for fast vec0 tests."""
    import os
    os.environ["EMBEDDING_DIM"] = "4"
    s = Storage(str(tmp_path / "test_vec.db"))
    yield s
    s.close()
    del os.environ["EMBEDDING_DIM"]


def test_insert_vec_and_search(storage_small_dim):
    s = storage_small_dim
    s.insert_note("id1", "ws1", "Notatka A", [], _now(), _now(), "treść A")
    s.insert_note("id2", "ws1", "Notatka B", [], _now(), _now(), "treść B")

    row1 = s._conn.execute("SELECT rowid FROM notes WHERE id='id1'").fetchone()
    row2 = s._conn.execute("SELECT rowid FROM notes WHERE id='id2'").fetchone()

    s.insert_vec("id1", row1[0], "ws1", _fake_embedding(0.1))
    s.insert_vec("id2", row2[0], "ws1", _fake_embedding(0.9))

    results = s.search_vec(_fake_embedding(0.1), "ws1", k=2)
    assert results[0]["id"] == "id1"  # nearest neighbor


def test_hybrid_search_fallback_without_vec(storage):
    storage.insert_note("id1", "ws1", "Python tutorial", [], _now(), _now(), "programowanie w Pythonie")
    # No vectors — hybrid should fall back to FTS
    results = storage.hybrid_search("Python", "ws1")
    assert any(r["id"] == "id1" for r in results)


def test_hybrid_search_with_vec(storage_small_dim):
    s = storage_small_dim
    s.insert_note("id1", "ws1", "Notatka wektorowa", [], _now(), _now(), "treść z wektorem")
    row = s._conn.execute("SELECT rowid FROM notes WHERE id='id1'").fetchone()
    s.insert_vec("id1", row[0], "ws1", _fake_embedding(0.5))

    results = s.hybrid_search("wektorowa", "ws1", embedding=_fake_embedding(0.5))
    assert any(r["id"] == "id1" for r in results)
