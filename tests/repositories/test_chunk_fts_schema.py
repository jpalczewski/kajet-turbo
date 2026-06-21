from sqlalchemy import text


def test_notes_fts_is_chunk_level(database):
    with database.engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(notes_fts)"))}
    assert {"chunk_id", "note_id", "workspace", "title", "header_path", "content"} <= cols


def test_notes_dropped_fts_rowid(database):
    with database.engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(notes)"))}
    assert "fts_rowid" not in cols
