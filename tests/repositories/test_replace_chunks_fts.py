from sqlalchemy import text as _text
from sqlmodel import Session

from kajet_turbo.markdown import Chunk
from kajet_turbo.models import Note
from kajet_turbo.repositories.notes import NoteChunkRepository


def _note(database, note_id="n1"):
    with Session(database.engine) as session:
        session.add(
            Note(
                id=note_id,
                workspace="ws",
                owner_id="u1",
                title="Title",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
        )
        session.commit()


def _chunks():
    return [
        Chunk(ordinal=0, header_path=["# Title"], content="alpha apple", char_start=0, char_end=11),
        Chunk(
            ordinal=1,
            header_path=["# Title", "## Sec"],
            content="beta banana",
            char_start=11,
            char_end=22,
        ),
    ]


def test_replace_chunks_writes_fts_rows(database):
    _note(database)
    repo = NoteChunkRepository(database.engine)
    repo.replace_chunks("n1", "ws", "u1", "Title", _chunks(), embeddings=None, dim=None)
    with Session(database.engine) as session:
        rows = session.execute(  # ty: ignore[deprecated] - raw SQL
            _text(
                "SELECT chunk_id, note_id, title, header_path, content"
                " FROM notes_fts WHERE note_id='n1'"
            )
        ).fetchall()
    assert len(rows) == 2
    assert {r.content for r in rows} == {"alpha apple", "beta banana"}
    assert any("Sec" in r.header_path for r in rows)
    assert all(r.title == "Title" for r in rows)


def test_replace_chunks_fts_is_searchable(database):
    _note(database)
    repo = NoteChunkRepository(database.engine)
    repo.replace_chunks("n1", "ws", "u1", "Title", _chunks(), embeddings=None, dim=None)
    with Session(database.engine) as session:
        hits = session.execute(  # ty: ignore[deprecated] - raw SQL
            _text("SELECT content FROM notes_fts WHERE notes_fts MATCH 'banana'")
        ).fetchall()
    assert [h.content for h in hits] == ["beta banana"]


def test_replace_chunks_replaces_fts_rows(database):
    _note(database)
    repo = NoteChunkRepository(database.engine)
    repo.replace_chunks("n1", "ws", "u1", "Title", _chunks(), embeddings=None, dim=None)
    repo.replace_chunks(
        "n1",
        "ws",
        "u1",
        "Title",
        [
            Chunk(
                ordinal=0, header_path=["# Title"], content="only gamma", char_start=0, char_end=10
            )
        ],
        embeddings=None,
        dim=None,
    )
    with Session(database.engine) as session:
        rows = session.execute(  # ty: ignore[deprecated] - raw SQL
            _text("SELECT content FROM notes_fts WHERE note_id='n1'")
        ).fetchall()
    assert [r.content for r in rows] == ["only gamma"]


def test_replace_chunks_empty_clears_fts(database):
    _note(database)
    repo = NoteChunkRepository(database.engine)
    repo.replace_chunks("n1", "ws", "u1", "Title", _chunks(), embeddings=None, dim=None)
    repo.replace_chunks("n1", "ws", "u1", "Title", [], embeddings=None, dim=None)
    with Session(database.engine) as session:
        rows = session.execute(  # ty: ignore[deprecated] - raw SQL
            _text("SELECT content FROM notes_fts WHERE note_id='n1'")
        ).fetchall()
    assert rows == []
