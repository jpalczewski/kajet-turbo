from sqlmodel import Session

from kajet_turbo.markdown import Chunk
from kajet_turbo.models import Note
from kajet_turbo.repositories.notes import NoteChunkRepository
from tests.helpers import vec_identity


def _note(note_id: str, owner_id: str = "u1", workspace: str = "ws", folder: str = "") -> Note:
    return Note(
        id=note_id,
        workspace=workspace,
        owner_id=owner_id,
        title=note_id,
        folder=folder,
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )


def _add_notes(database, *notes: Note) -> None:
    with Session(database.engine) as session:
        for note in notes:
            session.add(note)
        session.commit()


def test_related_chunks_ranks_closer_target_and_excludes_self_and_other_owner(database):
    repo = NoteChunkRepository(database.engine)
    identity = vec_identity(2)
    _add_notes(
        database,
        _note("src"),
        _note("near"),
        _note("far"),
        _note("theirs", owner_id="u2"),
    )
    repo.ensure_vec_table(identity)
    repo.replace_chunks(
        "src", "ws", "u1", "src", [Chunk(0, ["# S"], "s", 0, 1)], [[1.0, 0.0]], identity
    )
    repo.replace_chunks(
        "near", "ws", "u1", "near", [Chunk(0, ["# N"], "n", 0, 1)], [[0.9, 0.1]], identity
    )
    repo.replace_chunks(
        "far", "ws", "u1", "far", [Chunk(0, ["# F"], "f", 0, 1)], [[0.0, 1.0]], identity
    )
    # Same vector as the source, but owned by someone else — must never surface.
    repo.replace_chunks(
        "theirs", "ws", "u2", "theirs", [Chunk(0, ["# T"], "t", 0, 1)], [[1.0, 0.0]], identity
    )

    query = repo.related_chunks("src", "ws", "u1", identity)

    assert query.source_chunks_total == 1
    assert query.source_chunks_used == 1
    target_ids = [e.target_note_id for e in query.evidence]
    assert "src" not in target_ids
    assert "theirs" not in target_ids
    assert set(target_ids) == {"near", "far"}
    by_target = {e.target_note_id: e.distance for e in query.evidence}
    assert by_target["near"] < by_target["far"]


def test_related_chunks_empty_when_note_has_no_chunks(database):
    repo = NoteChunkRepository(database.engine)
    _add_notes(database, _note("src"))

    query = repo.related_chunks("src", "ws", "u1", vec_identity(2))

    assert query.source_chunks_total == 0
    assert query.source_chunks_used == 0
    assert query.evidence == []


def test_related_chunks_pending_when_chunks_exist_but_unembedded(database):
    repo = NoteChunkRepository(database.engine)
    identity = vec_identity(2)
    _add_notes(database, _note("src"))
    repo.ensure_vec_table(identity)
    # embeddings=None -> chunks-only write, note stays "stale", no vector row at all.
    repo.replace_chunks("src", "ws", "u1", "src", [Chunk(0, ["# S"], "s", 0, 1)], None, None)

    query = repo.related_chunks("src", "ws", "u1", identity)

    assert query.source_chunks_total == 1
    assert query.source_chunks_embedded == 0
    assert query.evidence == []


def test_related_chunks_ready_with_zero_items_when_nothing_else_embedded(database):
    """Distinguishes 'pending' from a genuinely empty 'ready' result: the source note
    itself IS embedded under the active identity, there is simply nothing else in the
    partition to match — source_chunks_embedded must stay positive even though evidence
    is empty, or the service would misreport this as pending."""
    repo = NoteChunkRepository(database.engine)
    identity = vec_identity(2)
    _add_notes(database, _note("src"))
    repo.ensure_vec_table(identity)
    repo.replace_chunks(
        "src", "ws", "u1", "src", [Chunk(0, ["# S"], "s", 0, 1)], [[1.0, 0.0]], identity
    )

    query = repo.related_chunks("src", "ws", "u1", identity)

    assert query.source_chunks_total == 1
    assert query.source_chunks_embedded == 1
    assert query.evidence == []


def test_related_chunks_stays_pending_after_identity_switch(database):
    """A note embedded under identity A, then queried under identity B (same dim, no
    re-embed yet): the src CTE's identity filter must reject A's vector as a query
    vector for B — proving the fix over the bench doc's literal SQL (which omitted the
    filter) and that no separate freshness table is needed (see #210's plan)."""
    repo = NoteChunkRepository(database.engine)
    identity_a = vec_identity(2, model="model-a")
    identity_b = vec_identity(2, model="model-b")
    _add_notes(database, _note("src"), _note("near"))
    repo.ensure_vec_table(identity_a)
    repo.replace_chunks(
        "src", "ws", "u1", "src", [Chunk(0, ["# S"], "s", 0, 1)], [[1.0, 0.0]], identity_a
    )
    repo.replace_chunks(
        "near", "ws", "u1", "near", [Chunk(0, ["# N"], "n", 0, 1)], [[0.9, 0.1]], identity_a
    )

    query = repo.related_chunks("src", "ws", "u1", identity_b)

    assert query.source_chunks_total == 1
    assert query.source_chunks_embedded == 0
    assert query.evidence == []


def test_related_chunks_folder_scope_narrows_candidates(database):
    repo = NoteChunkRepository(database.engine)
    identity = vec_identity(2)
    _add_notes(
        database,
        _note("src", folder="Journal"),
        _note("in_scope", folder="Journal/2026"),
        _note("out_of_scope", folder="Work"),
    )
    repo.ensure_vec_table(identity)
    repo.replace_chunks(
        "src", "ws", "u1", "src", [Chunk(0, ["# S"], "s", 0, 1)], [[1.0, 0.0]], identity
    )
    repo.replace_chunks(
        "in_scope", "ws", "u1", "in", [Chunk(0, ["# I"], "i", 0, 1)], [[0.9, 0.1]], identity
    )
    repo.replace_chunks(
        "out_of_scope", "ws", "u1", "out", [Chunk(0, ["# O"], "o", 0, 1)], [[0.9, 0.1]], identity
    )

    query = repo.related_chunks("src", "ws", "u1", identity, folder_note_ids={"in_scope"})

    assert [e.target_note_id for e in query.evidence] == ["in_scope"]


def test_related_chunks_missing_vec_table_degrades(database):
    repo = NoteChunkRepository(database.engine)
    _add_notes(database, _note("src"))
    # Chunks written with no embedding and no vec table ever created for this dim.
    repo.replace_chunks("src", "ws", "u1", "src", [Chunk(0, ["# S"], "s", 0, 1)], None, None)

    query = repo.related_chunks("src", "ws", "u1", vec_identity(999))

    assert query.source_chunks_total == 1
    assert query.source_chunks_embedded == 0
    assert query.evidence == []


def test_related_chunks_caps_source_chunks_at_sixteen(database):
    repo = NoteChunkRepository(database.engine)
    identity = vec_identity(2)
    _add_notes(database, _note("src"), _note("near"))
    repo.ensure_vec_table(identity)
    chunks = [Chunk(i, ["# S"], f"chunk {i}", i, i + 1) for i in range(20)]
    vectors = [[1.0, 0.0] for _ in range(20)]
    repo.replace_chunks("src", "ws", "u1", "src", chunks, vectors, identity)
    repo.replace_chunks(
        "near", "ws", "u1", "near", [Chunk(0, ["# N"], "n", 0, 1)], [[0.9, 0.1]], identity
    )

    query = repo.related_chunks("src", "ws", "u1", identity)

    assert query.source_chunks_total == 20
    assert query.source_chunks_used == 16
    assert query.source_chunks_embedded == 16
