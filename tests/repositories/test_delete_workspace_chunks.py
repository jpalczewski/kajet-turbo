from sqlalchemy import text as _text
from sqlmodel import Session

from kajet_turbo.markdown import Chunk
from kajet_turbo.models import Note
from kajet_turbo.repositories.notes import NoteChunkRepository, NoteRepository


def _note(database, note_id, owner="u1", ws="ws"):
    with Session(database.engine) as session:
        session.add(
            Note(
                id=note_id,
                workspace=ws,
                owner_id=owner,
                title="T",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
        )
        session.commit()


def _delete_workspace_notes(chunk_repo, note_repo, workspace, owner_id):
    """Delete all notes + their chunks for (workspace, owner_id).

    Chunks must be deleted before note rows (FK constraint: note_chunks.note_id → notes.id
    with no cascade). Both sub-repos share the same engine so we use a single session."""
    with Session(chunk_repo._engine) as session:
        chunk_repo.delete_for_workspace(workspace, owner_id, session)
        note_repo.delete_for_workspace(workspace, owner_id, session)
        session.commit()


def test_delete_workspace_notes_clears_chunks_and_does_not_fk_fail(database):
    # Regression: note_chunks FK to notes.id (no cascade) made delete_workspace_notes
    # FK-fail once chunks existed. It must clear chunks/FTS/vectors first.
    chunk_repo = NoteChunkRepository(database.engine)
    note_repo = NoteRepository(database.engine)
    _note(database, "n1")
    chunk_repo.ensure_vec_table(2)
    chunk_repo.replace_chunks(
        "n1", "ws", "u1", "T", [Chunk(0, ["# T"], "body", 0, 4)], [[0.1, 0.2]], 2
    )

    _delete_workspace_notes(chunk_repo, note_repo, "ws", "u1")  # must not raise

    assert chunk_repo.get_chunks("n1") == []
    with Session(database.engine) as session:
        fts = session.execute(_text("SELECT COUNT(*) FROM notes_fts WHERE note_id='n1'")).scalar()
        vec = session.execute(_text("SELECT COUNT(*) FROM note_chunks_vec_2")).scalar()
        notes = session.execute(_text("SELECT COUNT(*) FROM notes WHERE id='n1'")).scalar()
    assert fts == 0
    assert vec == 0
    assert notes == 0


def test_delete_workspace_notes_is_owner_scoped(database):
    # A shared workspace: deleting one owner's notes must leave the other owner's chunks/FTS.
    chunk_repo = NoteChunkRepository(database.engine)
    note_repo = NoteRepository(database.engine)
    _note(database, "n1", owner="u1")
    _note(database, "n2", owner="u2")
    chunk_repo.replace_chunks("n1", "ws", "u1", "T", [Chunk(0, ["# T"], "alpha", 0, 5)], None, None)
    chunk_repo.replace_chunks("n2", "ws", "u2", "T", [Chunk(0, ["# T"], "beta", 0, 4)], None, None)

    _delete_workspace_notes(chunk_repo, note_repo, "ws", "u1")

    assert chunk_repo.get_chunks("n1") == []
    assert len(chunk_repo.get_chunks("n2")) == 1
    with Session(database.engine) as session:
        kept = session.execute(_text("SELECT content FROM notes_fts WHERE note_id='n2'")).fetchall()
    assert [r.content for r in kept] == ["beta"]
