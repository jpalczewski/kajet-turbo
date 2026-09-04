import json

import pytest
from sqlalchemy import text as _text
from sqlmodel import Session

from kajet_turbo.markdown import Chunk
from kajet_turbo.models import Note
from kajet_turbo.repositories.notes import NoteChunkRepository


def _note(database, note_id="n1", ws="ws", owner="u1"):
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


def _chunks():
    return [
        Chunk(ordinal=0, header_path=["# T"], content="alpha body", char_start=0, char_end=10),
        Chunk(
            ordinal=1, header_path=["# T", "## S"], content="beta body", char_start=10, char_end=19
        ),
    ]


def test_replace_chunks_without_embeddings_marks_stale(database):
    _note(database)
    repo = NoteChunkRepository(database.engine)
    repo.replace_chunks("n1", "ws", "u1", "T", _chunks(), embeddings=None, dim=None)
    rows = repo.get_chunks("n1")
    assert [r["ordinal"] for r in rows] == [0, 1]
    assert rows[0]["content"] == "alpha body"
    assert json.loads(rows[1]["header_path"]) == ["# T", "## S"]
    with Session(database.engine) as session:
        note = session.get(Note, "n1")
        assert note is not None
        assert note.index_state == "stale"


def test_replace_chunks_with_embeddings_writes_vectors_and_marks_indexed(database):
    _note(database)
    repo = NoteChunkRepository(database.engine)
    repo.ensure_vec_table(2)
    repo.replace_chunks(
        "n1", "ws", "u1", "T", _chunks(), embeddings=[[0.1, 0.2], [0.3, 0.4]], dim=2
    )
    with Session(database.engine) as session:
        note = session.get(Note, "n1")
        vec_count = session.execute(  # ty: ignore[deprecated] - raw SQL
            _text("SELECT COUNT(*) FROM note_chunks_vec_2")
        ).scalar()
    assert note is not None
    assert note.index_state == "indexed"
    assert note.indexed_at is not None
    assert vec_count == 2
    assert all(r["dim"] == 2 for r in repo.get_chunks("n1"))


def test_replace_chunks_replaces_previous(database):
    _note(database)
    repo = NoteChunkRepository(database.engine)
    repo.ensure_vec_table(2)
    repo.replace_chunks(
        "n1", "ws", "u1", "T", _chunks(), embeddings=[[0.1, 0.2], [0.3, 0.4]], dim=2
    )
    repo.replace_chunks(
        "n1",
        "ws",
        "u1",
        "T",
        [Chunk(ordinal=0, header_path=["# T"], content="only", char_start=0, char_end=4)],
        embeddings=[[0.9, 0.9]],
        dim=2,
    )
    rows = repo.get_chunks("n1")
    assert len(rows) == 1 and rows[0]["content"] == "only"
    with Session(database.engine) as session:
        vec_count = session.execute(  # ty: ignore[deprecated] - raw SQL
            _text("SELECT COUNT(*) FROM note_chunks_vec_2 WHERE note_id='n1'")
        ).scalar()
    assert vec_count == 1


def test_replace_chunks_empty_clears(database):
    _note(database)
    repo = NoteChunkRepository(database.engine)
    repo.ensure_vec_table(2)
    repo.replace_chunks(
        "n1", "ws", "u1", "T", _chunks(), embeddings=[[0.1, 0.2], [0.3, 0.4]], dim=2
    )
    repo.replace_chunks("n1", "ws", "u1", "T", [], embeddings=None, dim=None)
    assert repo.get_chunks("n1") == []
    with Session(database.engine) as session:
        vec_count = session.execute(  # ty: ignore[deprecated] - raw SQL
            _text("SELECT COUNT(*) FROM note_chunks_vec_2 WHERE note_id='n1'")
        ).scalar()
    assert vec_count == 0


def test_replace_chunks_skips_superseded_revision(database):
    _note(database)
    repo = NoteChunkRepository(database.engine)
    original = [Chunk(ordinal=0, header_path=[], content="current", char_start=0, char_end=7)]
    stale = [Chunk(ordinal=0, header_path=[], content="stale", char_start=0, char_end=5)]
    assert repo.replace_chunks(
        "n1",
        "ws",
        "u1",
        "T",
        original,
        embeddings=None,
        dim=None,
        expected_generation=1,
    )
    with Session(database.engine) as session:
        note = session.get(Note, "n1")
        assert note is not None
        note.index_generation += 1
        session.add(note)
        session.commit()

    applied = repo.replace_chunks(
        "n1",
        "ws",
        "u1",
        "T",
        stale,
        embeddings=None,
        dim=None,
        expected_generation=1,
    )

    assert applied is False
    assert [row["content"] for row in repo.get_chunks("n1")] == ["current"]


def test_metadata_only_update_does_not_supersede_content_index(database):
    _note(database)
    repo = NoteChunkRepository(database.engine)
    with Session(database.engine) as session:
        note = session.get(Note, "n1")
        assert note is not None
        note.updated_at = "2026-01-02"
        session.add(note)
        session.commit()

    applied = repo.replace_chunks(
        "n1",
        "ws",
        "u1",
        "T",
        [Chunk(ordinal=0, header_path=[], content="current", char_start=0, char_end=7)],
        embeddings=None,
        dim=None,
        expected_generation=1,
    )

    assert applied is True
    assert [row["content"] for row in repo.get_chunks("n1")] == ["current"]


def test_replace_chunks_embedding_count_must_match(database):
    _note(database)
    repo = NoteChunkRepository(database.engine)
    repo.ensure_vec_table(2)
    with pytest.raises(ValueError):
        repo.replace_chunks("n1", "ws", "u1", "T", _chunks(), embeddings=[[0.1, 0.2]], dim=2)


def test_replace_chunks_in_session_on_superseded_leaves_caller_session_usable(database):
    # The _in_session split must NOT roll back on a CAS miss — that decision belongs to
    # whoever owns the session, since it may be one write among several in one transaction
    # (e.g. ReindexNoteHandler committing a chunk replace + an embed_note enqueue together).
    _note(database)
    _note(database, note_id="n2")
    repo = NoteChunkRepository(database.engine)
    original = [Chunk(ordinal=0, header_path=[], content="current", char_start=0, char_end=7)]
    repo.replace_chunks("n1", "ws", "u1", "T", original, embeddings=None, dim=None)
    with Session(database.engine) as session:
        note = session.get(Note, "n1")
        assert note is not None
        note.index_generation += 1
        session.add(note)
        session.commit()

    with Session(database.engine) as session:
        # An earlier write in the same caller-owned transaction, uncommitted so far.
        other = session.get(Note, "n2")
        assert other is not None
        other.title = "renamed before the superseded write"
        session.add(other)

        applied = NoteChunkRepository.replace_chunks_in_session(
            session,
            "n1",
            "ws",
            "u1",
            "T",
            [Chunk(ordinal=0, header_path=[], content="stale", char_start=0, char_end=5)],
            None,
            None,
            expected_generation=1,
        )
        assert applied is False
        # No self-rollback: the caller's session is still open and n2's staged change is
        # still pending — the caller decides what happens to it (commit or roll back).
        session.commit()

    with Session(database.engine) as session:
        n2 = session.get(Note, "n2")
        assert n2 is not None and n2.title == "renamed before the superseded write"
    assert [row["content"] for row in repo.get_chunks("n1")] == ["current"]
