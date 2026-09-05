import pytest
from sqlalchemy import text

from kajet_turbo.repositories.notes import NoteChunkRepository
from tests.helpers import vec_identity


def test_ensure_vec_table_creates_dim_table(database):
    repo = NoteChunkRepository(database.engine)
    repo.ensure_vec_table(vec_identity(768))
    with database.engine.connect() as conn:
        names = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE name LIKE 'note_chunks_vec_%'")
            )
        }
    assert "note_chunks_vec_768" in names


def test_ensure_vec_table_is_idempotent(database):
    repo = NoteChunkRepository(database.engine)
    repo.ensure_vec_table(vec_identity(1024))
    repo.ensure_vec_table(vec_identity(1024))  # no error second time
    with database.engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM sqlite_master WHERE name = 'note_chunks_vec_1024'")
        ).scalar()
    assert count == 1


def test_ensure_vec_table_rejects_non_int_dim(database):
    repo = NoteChunkRepository(database.engine)
    with pytest.raises((ValueError, TypeError)):
        repo.ensure_vec_table(vec_identity("768; DROP TABLE notes"))  # ty: ignore[invalid-argument-type]


def test_ensure_vec_table_declares_identity_partition_and_small_blocks(database):
    """The #37 storage shape. ensure_vec_table uses IF NOT EXISTS, so this DDL only ever
    reaches an existing database through the Alembic rebuild — the two must agree."""
    repo = NoteChunkRepository(database.engine)
    repo.ensure_vec_table(vec_identity(4))
    with database.engine.connect() as conn:
        ddl = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'note_chunks_vec_4'")
        ).scalar_one()
    assert "workspace TEXT partition key" in ddl
    assert "identity TEXT partition key" in ddl
    assert "chunk_size=64" in ddl
