import pytest
from sqlalchemy import text as _text
from sqlmodel import Session

from kajet_turbo.markdown import Chunk
from kajet_turbo.models import Note
from kajet_turbo.repositories.notes import NoteChunkRepository
from tests.helpers import vec_identity


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


def _stale_note_with_chunks(database) -> NoteChunkRepository:
    _note(database)
    repo = NoteChunkRepository(database.engine)
    repo.replace_chunks("n1", "ws", "u1", "T", _chunks(), embeddings=None, identity=None)
    return repo


def _vec_count(database, dim: int, note_id: str = "n1") -> int:
    with Session(database.engine) as session:
        return session.execute(  # ty: ignore[deprecated] - raw SQL
            _text(f"SELECT COUNT(*) FROM note_chunks_vec_{dim} WHERE note_id = :nid"),
            {"nid": note_id},
        ).scalar_one()


def test_attach_vectors_writes_vectors_and_marks_indexed(database):
    repo = _stale_note_with_chunks(database)
    repo.ensure_vec_table(vec_identity(2))
    rows = repo.get_chunks("n1")
    vectors = {r["id"]: [0.1 * (i + 1), 0.2] for i, r in enumerate(rows)}

    applied = repo.attach_vectors("n1", "ws", "u1", vec_identity(2), vectors)

    assert applied is True
    assert _vec_count(database, 2) == 2
    assert all(r["dim"] == 2 for r in repo.get_chunks("n1"))
    with Session(database.engine) as session:
        note = session.get(Note, "n1")
        assert note is not None
        assert note.index_state == "indexed"
        assert note.indexed_at is not None


def test_attach_vectors_chunk_drift_returns_false_and_stays_stale(database):
    repo = _stale_note_with_chunks(database)
    repo.ensure_vec_table(vec_identity(2))
    rows = repo.get_chunks("n1")
    # Simulate a concurrent edit between the handler's read and the attach: the
    # vectors reference a chunk id that no longer matches the stored set.
    vectors = {rows[0]["id"]: [0.1, 0.2], "gone-chunk-id": [0.3, 0.4]}

    applied = repo.attach_vectors("n1", "ws", "u1", vec_identity(2), vectors)

    assert applied is False
    assert _vec_count(database, 2) == 0
    with Session(database.engine) as session:
        note = session.get(Note, "n1")
        assert note is not None
        assert note.index_state == "stale"


def test_attach_vectors_missing_chunk_returns_false(database):
    repo = _stale_note_with_chunks(database)
    repo.ensure_vec_table(vec_identity(2))
    rows = repo.get_chunks("n1")
    # Subset of the stored chunk set is also drift — one vector missing.
    vectors = {rows[0]["id"]: [0.1, 0.2]}

    assert repo.attach_vectors("n1", "ws", "u1", vec_identity(2), vectors) is False
    assert _vec_count(database, 2) == 0


def test_attach_vectors_purges_old_dim_vectors(database):
    repo = _stale_note_with_chunks(database)
    repo.ensure_vec_table(vec_identity(2))
    repo.ensure_vec_table(vec_identity(3))
    rows = repo.get_chunks("n1")
    assert (
        repo.attach_vectors("n1", "ws", "u1", vec_identity(2), {r["id"]: [0.1, 0.2] for r in rows})
        is True
    )

    # Backend switch to a different dim: re-attach against the same chunk rows.
    applied = repo.attach_vectors(
        "n1", "ws", "u1", vec_identity(3), {r["id"]: [0.1, 0.2, 0.3] for r in rows}
    )

    assert applied is True
    assert _vec_count(database, 2) == 0
    assert _vec_count(database, 3) == 2
    assert all(r["dim"] == 3 for r in repo.get_chunks("n1"))


def test_attach_vectors_no_chunks_returns_false(database):
    _note(database)
    repo = NoteChunkRepository(database.engine)
    repo.ensure_vec_table(vec_identity(2))
    assert repo.attach_vectors("n1", "ws", "u1", vec_identity(2), {}) is False


def test_attach_vectors_rejects_non_int_dim(database):
    repo = _stale_note_with_chunks(database)
    rows = repo.get_chunks("n1")
    with pytest.raises(ValueError):
        repo.attach_vectors(
            "n1",
            "ws",
            "u1",
            vec_identity("2; DROP TABLE notes"),  # ty: ignore[invalid-argument-type] — injection guard under test
            {r["id"]: [0.1] for r in rows},
        )
