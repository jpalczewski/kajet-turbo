from sqlmodel import Session

from kajet_turbo.embedding.cache import pack_vector
from kajet_turbo.markdown import Chunk
from kajet_turbo.models import Note
from kajet_turbo.repositories.notes import NoteChunkRepository


def _seed(database):
    repo = NoteChunkRepository(database.engine)
    with Session(database.engine) as session:
        session.add(
            Note(
                id="n1",
                workspace="ws",
                owner_id="u1",
                title="Fruit",
                folder="f",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
        )
        session.add(
            Note(
                id="n2",
                workspace="ws",
                owner_id="u1",
                title="Veg",
                folder="",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
        )
        session.commit()
    repo.replace_chunks(
        "n1", "ws", "u1", "Fruit", [Chunk(0, ["# Fruit"], "apple banana cherry", 0, 19)], None, None
    )
    repo.replace_chunks(
        "n2", "ws", "u1", "Veg", [Chunk(0, ["# Veg"], "carrot potato", 0, 13)], None, None
    )
    return repo


def test_search_fts_returns_chunk_shape(database):
    repo = _seed(database)
    hits = repo.search_fts("banana", "ws", "u1", limit=10)
    assert len(hits) == 1
    h = hits[0]
    assert h["note_id"] == "n1"
    assert h["title"] == "Fruit"
    assert h["folder"] == "f"
    assert h["header_path"] == ["# Fruit"]
    assert "banana" in h["content"]


def test_search_fts_owner_scoped(database):
    repo = _seed(database)
    # different owner sees nothing
    assert repo.search_fts("banana", "ws", "other", limit=10) == []


def test_hybrid_search_fts_only_returns_chunks(database):
    repo = _seed(database)
    hits = repo.hybrid_search("carrot", "ws", "u1", embedding=None, limit=10)
    assert [h["note_id"] for h in hits] == ["n2"]
    assert hits[0]["header_path"] == ["# Veg"]
    assert "chunk_id" not in hits[0]  # internal field stripped from public shape


def test_hybrid_search_returns_updated_at(database):
    repo = _seed(database)
    hits = repo.hybrid_search("banana", "ws", "u1", embedding=None, limit=10)
    assert hits[0]["updated_at"] == "2026-01-01"


def test_hybrid_search_meta_hit_boosts_existing_chunk(database):
    repo = _seed(database)
    # "Fruit" (n1) already ranks via FTS for "banana" — a meta hit for the same note must
    # boost its score and attach matched_on, not duplicate the row.
    meta_hits = [
        {
            "note_id": "n1",
            "title": "Fruit",
            "folder": "f",
            "updated_at": "2026-01-01",
            "matched_on": ["tag"],
        }
    ]
    hits = repo.hybrid_search("banana", "ws", "u1", embedding=None, limit=10, meta_hits=meta_hits)
    assert len(hits) == 1
    assert hits[0]["note_id"] == "n1"
    assert hits[0]["matched_on"] == ["tag"]


def test_hybrid_search_synthesizes_row_for_note_without_chunks(database):
    from kajet_turbo.models import Note

    repo = _seed(database)
    with Session(database.engine) as session:
        session.add(
            Note(
                id="n3",
                workspace="ws",
                owner_id="u1",
                title="Empty note",
                folder="",
                created_at="2026-01-01",
                updated_at="2026-01-02",
            )
        )
        session.commit()
    meta_hits = [
        {
            "note_id": "n3",
            "title": "Empty note",
            "folder": "",
            "updated_at": "2026-01-02",
            "matched_on": ["title"],
        }
    ]
    # Query that matches nothing in FTS (n3 has zero chunks — the exact "Angelika" bug shape:
    # a note whose only match is metadata, never indexed as a chunk) — must still surface.
    hits = repo.hybrid_search(
        "zzznomatch", "ws", "u1", embedding=None, limit=10, meta_hits=meta_hits
    )
    assert [h["note_id"] for h in hits] == ["n3"]
    assert hits[0]["header_path"] == []
    assert hits[0]["content"] == ""
    assert hits[0]["matched_on"] == ["title"]
    assert hits[0]["title"] == "Empty note"


def test_hybrid_search_without_meta_hits_has_none_matched_on(database):
    repo = _seed(database)
    hits = repo.hybrid_search("banana", "ws", "u1", embedding=None, limit=10)
    assert hits[0]["matched_on"] is None


def test_search_chunks_vec_knn(database):
    repo = NoteChunkRepository(database.engine)
    with Session(database.engine) as session:
        session.add(
            Note(
                id="n1",
                workspace="ws",
                owner_id="u1",
                title="Fruit",
                folder="",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
        )
        session.add(
            Note(
                id="n2",
                workspace="ws",
                owner_id="u1",
                title="Veg",
                folder="",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
        )
        session.commit()
    repo.ensure_vec_table(2)
    repo.replace_chunks(
        "n1", "ws", "u1", "Fruit", [Chunk(0, ["# Fruit"], "apple", 0, 5)], [[1.0, 0.0]], 2
    )
    repo.replace_chunks(
        "n2", "ws", "u1", "Veg", [Chunk(0, ["# Veg"], "carrot", 0, 6)], [[0.0, 1.0]], 2
    )
    hits = repo.search_chunks_vec(pack_vector([1.0, 0.0]), "ws", "u1", dim=2, k=10)
    assert hits[0]["note_id"] == "n1"
    assert hits[0]["header_path"] == ["# Fruit"]


def test_search_chunks_vec_missing_table_degrades(database):
    # A backend configured before any indexing at its dim → no vec table yet. Must return []
    # (degrade to FTS) instead of raising "no such table".
    repo = NoteChunkRepository(database.engine)
    assert repo.search_chunks_vec(pack_vector([1.0, 0.0]), "ws", "u1", dim=999, k=10) == []


def test_hybrid_search_caps_chunks_per_note(database):
    repo = NoteChunkRepository(database.engine)
    with Session(database.engine) as session:
        session.add(
            Note(
                id="n1",
                workspace="ws",
                owner_id="u1",
                title="Big",
                folder="",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
        )
        session.commit()
    chunks = [Chunk(i, ["# Big"], f"shared keyword piece {i}", i, i + 1) for i in range(6)]
    repo.replace_chunks("n1", "ws", "u1", "Big", chunks, None, None)
    hits = repo.hybrid_search("keyword", "ws", "u1", embedding=None, limit=10, per_note_cap=2)
    assert sum(1 for h in hits if h["note_id"] == "n1") <= 2


def test_hybrid_search_allowed_note_ids_filters_all_candidate_lists(database):
    repo = _seed(database)
    meta_hits = [
        {
            "note_id": "n2",
            "title": "Veg",
            "folder": "",
            "updated_at": "2026-01-01",
            "matched_on": ["title"],
        }
    ]
    # "n1" (Fruit) would match FTS for "apple"; only "n2" is allowed — n1 must be excluded
    # even though it ranks via full-text, and the meta hit for n2 (which has no FTS match
    # for "apple") must still surface since n2 is in allowed_note_ids.
    hits = repo.hybrid_search(
        "apple",
        "ws",
        "u1",
        embedding=None,
        limit=10,
        meta_hits=meta_hits,
        allowed_note_ids={"n2"},
    )
    assert [h["note_id"] for h in hits] == ["n2"]
