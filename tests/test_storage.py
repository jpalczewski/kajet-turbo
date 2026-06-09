import struct
from datetime import UTC, datetime

import pytest

from kajet_turbo.db import Database
from kajet_turbo.repositories.notes import NoteRepository


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    yield d
    d.close()


@pytest.fixture
def notes(db):
    return NoteRepository(db.engine)


def test_schema_creates_all_tables(db):
    conn = db._conn
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "notes" in names
    assert "oauth_clients" in names
    assert "users" in names
    assert "workspace_access" in names


def test_schema_creates_virtual_tables(db):
    conn = db._conn
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master"
    ).fetchall()}
    assert any(n.startswith("notes_fts") for n in names)
    assert any(n.startswith("notes_vec") for n in names)


def test_vec_version_loaded(db):
    version = db._conn.execute("SELECT vec_version()").fetchone()[0]
    assert version.startswith("v")


def test_wal_mode_enabled(db):
    mode = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def test_insert_and_get_note(notes):
    notes.insert("abc1234", "ws1", "u1", "Moja notatka", ["python"], _now(), _now(), "treść notatki")
    note = notes.get("abc1234")
    assert note.id == "abc1234"
    assert note.title == "Moja notatka"
    assert note.workspace == "ws1"
    assert note.owner_id == "u1"


def test_get_note_returns_none_for_missing(notes):
    assert notes.get("nieistnieje") is None


def test_update_note(notes):
    notes.insert("abc1234", "ws1", "u1", "Stary tytuł", [], _now(), _now(), "treść")
    notes.update("abc1234", title="Nowy tytuł", content="nowa treść", updated_at=_now())
    note = notes.get("abc1234")
    assert note.title == "Nowy tytuł"


def test_delete_note(notes):
    notes.insert("abc1234", "ws1", "u1", "Do usunięcia", [], _now(), _now(), "treść")
    notes.delete("abc1234")
    assert notes.get("abc1234") is None


def test_list_notes_by_workspace(notes):
    notes.insert("id1", "ws1", "u1", "Notatka 1", ["a"], _now(), _now(), "treść 1")
    notes.insert("id2", "ws1", "u1", "Notatka 2", ["b"], _now(), _now(), "treść 2")
    notes.insert("id3", "ws2", "u1", "Notatka 3", [], _now(), _now(), "treść 3")
    result = notes.list("ws1", owner_id="u1")
    ids = [n["note_id"] for n in result]
    assert "id1" in ids
    assert "id2" in ids
    assert "id3" not in ids


def test_list_notes_filter_by_tag(notes):
    notes.insert("id1", "ws1", "u1", "Tagged", ["python", "mcp"], _now(), _now(), "treść")
    notes.insert("id2", "ws1", "u1", "Untagged", [], _now(), _now(), "treść")
    result = notes.list("ws1", owner_id="u1", tags=["python"])
    ids = [n["note_id"] for n in result]
    assert "id1" in ids
    assert "id2" not in ids


def test_list_notes_isolated_by_owner(notes):
    notes.insert("id1", "ws1", "u1", "Notatka u1", [], _now(), _now(), "treść")
    notes.insert("id2", "ws1", "u2", "Notatka u2", [], _now(), _now(), "treść")
    result_u1 = notes.list("ws1", owner_id="u1")
    result_u2 = notes.list("ws1", owner_id="u2")
    assert [n["note_id"] for n in result_u1] == ["id1"]
    assert [n["note_id"] for n in result_u2] == ["id2"]


def test_fts_search_finds_by_title(notes):
    notes.insert("id1", "ws1", "u1", "Python async programming", ["python"], _now(), _now(), "tutorial o asyncio")
    notes.insert("id2", "ws1", "u1", "JavaScript basics", ["js"], _now(), _now(), "podstawy JS")
    results = notes.search_fts("async", "ws1", owner_id="u1")
    ids = [r["note_id"] for r in results]
    assert "id1" in ids
    assert "id2" not in ids


def test_fts_search_finds_by_content(notes):
    notes.insert("id1", "ws1", "u1", "Notatka", [], _now(), _now(), "sqlite jest świetny do embeddingów")
    results = notes.search_fts("embedding", "ws1", owner_id="u1")
    assert any(r["note_id"] == "id1" for r in results)


def test_fts_search_respects_workspace(notes):
    notes.insert("id1", "ws1", "u1", "Python notatka", [], _now(), _now(), "treść")
    notes.insert("id2", "ws2", "u1", "Python inny workspace", [], _now(), _now(), "treść")
    results = notes.search_fts("Python", "ws1", owner_id="u1")
    ids = [r["note_id"] for r in results]
    assert "id1" in ids
    assert "id2" not in ids


def test_fts_search_respects_owner(notes):
    notes.insert("id1", "ws1", "u1", "Python notatka u1", [], _now(), _now(), "treść")
    notes.insert("id2", "ws1", "u2", "Python notatka u2", [], _now(), _now(), "treść")
    results = notes.search_fts("Python", "ws1", owner_id="u1")
    ids = [r["note_id"] for r in results]
    assert "id1" in ids
    assert "id2" not in ids


def test_fts_search_trigram_partial(notes):
    notes.insert("id1", "ws1", "u1", "Programowanie", [], _now(), _now(), "nauka programowania w Pythonie")
    results = notes.search_fts("gram", "ws1", owner_id="u1")
    assert any(r["note_id"] == "id1" for r in results)


def test_update_note_title_only_preserves_fts_content(notes):
    notes.insert("abc1234", "ws1", "u1", "Stary tytuł", [], _now(), _now(), "unikalna treść notatki")
    notes.update("abc1234", title="Nowy tytuł", updated_at=_now())
    results = notes.search_fts("unikalna", "ws1", owner_id="u1")
    assert any(r["note_id"] == "abc1234" for r in results)


def _fake_embedding(val: float, dim: int = 4) -> bytes:
    return struct.pack(f"{dim}f", *[val] * dim)


@pytest.fixture
def db_small_dim(tmp_path):
    import os
    os.environ["EMBEDDING_DIM"] = "4"
    d = Database(str(tmp_path / "test_vec.db"))
    yield d
    d.close()
    del os.environ["EMBEDDING_DIM"]


def test_insert_vec_and_search(db_small_dim):
    repo = NoteRepository(db_small_dim.engine)
    repo.insert("id1", "ws1", "u1", "Notatka A", [], _now(), _now(), "treść A")
    repo.insert("id2", "ws1", "u1", "Notatka B", [], _now(), _now(), "treść B")

    row1 = db_small_dim._conn.execute("SELECT rowid FROM notes WHERE id='id1'").fetchone()
    row2 = db_small_dim._conn.execute("SELECT rowid FROM notes WHERE id='id2'").fetchone()

    repo.insert_vec("id1", row1[0], "ws1", _fake_embedding(0.1))
    repo.insert_vec("id2", row2[0], "ws1", _fake_embedding(0.9))

    results = repo.search_vec(_fake_embedding(0.1), "ws1", owner_id="u1", k=2)
    assert results[0]["note_id"] == "id1"


def test_hybrid_search_fallback_without_vec(notes):
    notes.insert("id1", "ws1", "u1", "Python tutorial", [], _now(), _now(), "programowanie w Pythonie")
    results = notes.hybrid_search("Python", "ws1", owner_id="u1")
    assert any(r["note_id"] == "id1" for r in results)


def test_hybrid_search_with_vec(db_small_dim):
    repo = NoteRepository(db_small_dim.engine)
    repo.insert("id1", "ws1", "u1", "Notatka wektorowa", [], _now(), _now(), "treść z wektorem")
    row = db_small_dim._conn.execute("SELECT rowid FROM notes WHERE id='id1'").fetchone()
    repo.insert_vec("id1", row[0], "ws1", _fake_embedding(0.5))

    results = repo.hybrid_search("wektorowa", "ws1", owner_id="u1", embedding=_fake_embedding(0.5))
    assert any(r["note_id"] == "id1" for r in results)
