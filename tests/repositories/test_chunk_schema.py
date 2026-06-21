from sqlalchemy import text


def test_chunk_tables_and_columns(database):
    with database.engine.connect() as conn:
        names = {
            r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        assert "note_chunks" in names
        assert "index_meta" in names
        chunk_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(note_chunks)"))}
        note_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(notes)"))}
    assert {
        "chunk_rowid",
        "id",
        "note_id",
        "workspace",
        "owner_id",
        "ordinal",
        "header_path",
        "content",
        "char_start",
        "char_end",
        "dim",
        "created_at",
    } <= chunk_cols
    assert {"index_state", "indexed_at"} <= note_cols


def test_notes_vec_is_gone(database):
    with database.engine.connect() as conn:
        names = {
            r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
    assert "notes_vec" not in names


def test_note_level_fts_still_present(database):
    with database.engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(notes_fts)"))}
    assert {"note_id", "workspace", "title", "content"} <= cols
