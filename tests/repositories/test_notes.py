from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from kajet_turbo.db import Database
from kajet_turbo.markdown import Chunk
from kajet_turbo.repositories.notes import NoteRepository


@pytest.fixture
def db(database: Database) -> Database:
    return database


@pytest.fixture
def notes(db):
    return NoteRepository(db.engine)


def test_schema_creates_all_tables(db):
    with db.engine.connect() as conn:
        names = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
    assert "notes" in names
    assert "oauth_clients" in names
    assert "users" in names
    assert "workspace_access" in names


def test_schema_creates_virtual_tables(db):
    with db.engine.connect() as conn:
        names = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master")).fetchall()}
    assert any(n.startswith("notes_fts") for n in names)


def test_vec_version_loaded(db):
    with db.engine.connect() as conn:
        version = conn.execute(text("SELECT vec_version()")).fetchone()[0]
    assert version.startswith("v")


def test_wal_mode_enabled(db):
    with db.engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).fetchone()[0]
    assert mode == "wal"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def test_insert_and_get_note(notes):
    notes.insert(
        "abc1234", "ws1", "u1", "Moja notatka", ["python"], _now(), _now(), "treść notatki"
    )
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
    # list() now filters via the tag index (not JSON field); sync_note_tags populates it.
    notes.sync_note_tags("id1", "ws1", "u1", [("python", "frontmatter"), ("mcp", "frontmatter")])
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


def test_list_notes_folder_readme_first_natural_order(notes):
    # updated_at deliberately reversed vs. the desired display order, so a wrong
    # sort (recency) would be caught. "10" vs "02" also catches lexicographic sort.
    notes.insert("id01", "ws1", "u1", "01-intro", [], _now(), "2026-01-04", "x", folder="ch1")
    notes.insert("id02", "ws1", "u1", "02-body", [], _now(), "2026-01-03", "x", folder="ch1")
    notes.insert("id10", "ws1", "u1", "10-end", [], _now(), "2026-01-02", "x", folder="ch1")
    notes.insert("idrd", "ws1", "u1", "README", [], _now(), "2026-01-01", "x", folder="ch1")
    result = notes.list("ws1", owner_id="u1", folder="ch1")
    assert [n["note_id"] for n in result] == ["idrd", "id01", "id02", "id10"]


def test_list_notes_no_folder_keeps_recency_order(notes):
    notes.insert("idA", "ws1", "u1", "README", [], _now(), "2026-01-01", "x", folder="ch1")
    notes.insert("idB", "ws1", "u1", "01-intro", [], _now(), "2026-01-09", "x", folder="ch1")
    result = notes.list("ws1", owner_id="u1")  # folder=None -> recent first
    assert [n["note_id"] for n in result] == ["idB", "idA"]


def test_list_notes_limit_none_returns_all(notes):
    for i in range(25):
        notes.insert(f"id{i:02d}", "ws1", "u1", f"{i:02d}-note", [], _now(), _now(), "x")
    assert len(notes.list("ws1", owner_id="u1", limit=None)) == 25
    assert len(notes.list("ws1", owner_id="u1")) == 20  # default cap unchanged


def _index(notes, note_id, workspace, owner_id, title, content):
    """Create a note row and a single FTS-indexed chunk for it (insert writes no FTS)."""
    notes.insert(note_id, workspace, owner_id, title, [], _now(), _now(), content)
    notes.replace_chunks(
        note_id,
        workspace,
        owner_id,
        title,
        [Chunk(0, [f"# {title}"], content, 0, len(content))],
        None,
        None,
    )


def test_fts_search_finds_by_title(notes):
    # FTS now indexes the chunk's header_path (the title-derived "# ..." breadcrumb).
    _index(notes, "id1", "ws1", "u1", "Python async programming", "tutorial o asyncio")
    _index(notes, "id2", "ws1", "u1", "JavaScript basics", "podstawy JS")
    results = notes.search_fts("async", "ws1", owner_id="u1")
    ids = [r["note_id"] for r in results]
    assert "id1" in ids
    assert "id2" not in ids


def test_fts_search_finds_by_content(notes):
    _index(notes, "id1", "ws1", "u1", "Notatka", "sqlite jest świetny do embeddingów")
    results = notes.search_fts("embedding", "ws1", owner_id="u1")
    assert any(r["note_id"] == "id1" for r in results)
    assert all("content" in r for r in results)


def test_fts_search_respects_workspace(notes):
    _index(notes, "id1", "ws1", "u1", "Python notatka", "treść o pythonie")
    _index(notes, "id2", "ws2", "u1", "Python inny workspace", "treść o pythonie")
    results = notes.search_fts("python", "ws1", owner_id="u1")
    ids = [r["note_id"] for r in results]
    assert "id1" in ids
    assert "id2" not in ids


def test_fts_search_respects_owner(notes):
    _index(notes, "id1", "ws1", "u1", "Python notatka u1", "treść o pythonie")
    _index(notes, "id2", "ws1", "u2", "Python notatka u2", "treść o pythonie")
    results = notes.search_fts("python", "ws1", owner_id="u1")
    ids = [r["note_id"] for r in results]
    assert "id1" in ids
    assert "id2" not in ids


def test_fts_search_trigram_partial(notes):
    _index(notes, "id1", "ws1", "u1", "Programowanie", "nauka programowania w Pythonie")
    results = notes.search_fts("gram", "ws1", owner_id="u1")
    assert any(r["note_id"] == "id1" for r in results)


def test_hybrid_search_fallback_without_vec(notes):
    _index(notes, "id1", "ws1", "u1", "Python tutorial", "programowanie w Pythonie")
    results = notes.hybrid_search("python", "ws1", owner_id="u1", embedding=None)
    assert any(r["note_id"] == "id1" for r in results)


def test_get_by_path_root_and_folder(notes):
    notes.insert("id1", "ws1", "u1", "Plan", [], _now(), _now(), "treść", folder="")
    notes.insert("id2", "ws1", "u1", "Plan", [], _now(), _now(), "treść", folder="Projekty")
    root = notes.get_by_path("ws1", "u1", "", "Plan")
    nested = notes.get_by_path("ws1", "u1", "Projekty", "Plan")
    assert root is not None and root.id == "id1"
    assert nested is not None and nested.id == "id2"


def test_get_by_path_missing_returns_none(notes):
    notes.insert("id1", "ws1", "u1", "Plan", [], _now(), _now(), "treść")
    assert notes.get_by_path("ws1", "u1", "Inny", "Plan") is None
    assert notes.get_by_path("ws1", "u2", "", "Plan") is None


def test_resolve_paths_batch(notes):
    notes.insert("id1", "ws1", "u1", "Plan", [], _now(), _now(), "t", folder="A")
    notes.insert("id2", "ws1", "u1", "Notes", [], _now(), _now(), "t", folder="")
    resolved = notes.resolve_paths("ws1", "u1", [("A", "Plan"), ("", "Notes"), ("", "Ghost")])
    assert resolved == {("A", "Plan"): "id1", ("", "Notes"): "id2"}


def test_resolve_paths_empty(notes):
    assert notes.resolve_paths("ws1", "u1", []) == {}


def test_insert_writes_no_fts_rows(database):
    from sqlalchemy import text

    from kajet_turbo.repositories.notes import NoteRepository

    repo = NoteRepository(database.engine)
    repo.insert("n1", "ws", "u1", "Title", [], "2026-01-01", "2026-01-01", "body", "")
    with database.engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM notes_fts WHERE note_id='n1'")).scalar()
    assert n == 0  # FTS is now written only via replace_chunks (chunk-level)


# --- add_link tests ---


def test_add_link_inserts_single_edge(notes):
    notes.insert("a", "ws", "u1", "A", [], _now(), _now(), "body")
    notes.insert("b", "ws", "u1", "B", [], _now(), _now(), "body")
    notes.add_link("b", "a", "ws", "u1")
    assert "a" in notes.outlinks("b")


def test_add_link_idempotent(notes):
    notes.insert("a", "ws", "u1", "A", [], _now(), _now(), "body")
    notes.insert("b", "ws", "u1", "B", [], _now(), _now(), "body")
    notes.add_link("b", "a", "ws", "u1")
    notes.add_link("b", "a", "ws", "u1")  # second insert must not raise
    assert notes.outlinks("b") == ["a"]


def test_add_link_preserves_existing_edges(notes):
    for nid, title in [("a", "A"), ("b", "B"), ("c", "C")]:
        notes.insert(nid, "ws", "u1", title, [], _now(), _now(), "body")
    notes.replace_links("a", "ws", "u1", {"b"})  # a -> b
    notes.add_link("a", "c", "ws", "u1")  # add a -> c without dropping a -> b
    assert set(notes.outlinks("a")) == {"b", "c"}
