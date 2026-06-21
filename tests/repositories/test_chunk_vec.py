import pytest
from sqlalchemy import text

from kajet_turbo.repositories.notes import NoteRepository


def test_ensure_vec_table_creates_dim_table(database):
    repo = NoteRepository(database.engine)
    repo.ensure_vec_table(768)
    with database.engine.connect() as conn:
        names = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE name LIKE 'note_chunks_vec_%'")
            )
        }
    assert "note_chunks_vec_768" in names


def test_ensure_vec_table_is_idempotent(database):
    repo = NoteRepository(database.engine)
    repo.ensure_vec_table(1024)
    repo.ensure_vec_table(1024)  # no error second time
    with database.engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM sqlite_master WHERE name = 'note_chunks_vec_1024'")
        ).scalar()
    assert count == 1


def test_ensure_vec_table_rejects_non_int_dim(database):
    repo = NoteRepository(database.engine)
    with pytest.raises((ValueError, TypeError)):
        repo.ensure_vec_table("768; DROP TABLE notes")  # ty: ignore[invalid-argument-type]
