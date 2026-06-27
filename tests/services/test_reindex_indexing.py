from sqlmodel import Session

from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.cache import EmbeddingCacheRepository
from kajet_turbo.models import Note
from kajet_turbo.repositories.notes import NoteChunkRepository
from kajet_turbo.services.indexing import NoteIndexer


class _FakeEmbedder:
    name = "fake"
    dim = 3
    query_prefix = ""
    passage_prefix = ""

    async def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    async def embed_query(self, text):
        return [1.0, 0.0, 0.0]


def _indexer(database):
    repo = NoteChunkRepository(database.engine)
    cache = EmbeddingCacheRepository(database.engine)
    cfg = EmbedderConfig(
        backend_id="fake", type="fake", model="m", dim=3, base_url="http://x", api_key="k"
    )
    return NoteIndexer(repo, cache, lambda o: cfg, lambda c: _FakeEmbedder())


def _seed_notes(database, n=3):
    with Session(database.engine) as session:
        for i in range(n):
            session.add(
                Note(
                    id=f"n{i}",
                    workspace="ws",
                    owner_id="u1",
                    title=f"T{i}",
                    created_at="2026-01-01",
                    updated_at="2026-01-01",
                )
            )
        session.commit()


def test_index_many_indexes_all(database):
    _seed_notes(database, 3)
    repo = NoteChunkRepository(database.engine)
    indexer = _indexer(database)
    notes = [
        {"id": f"n{i}", "title": f"T{i}", "content": f"# T{i}\n\nbody {i}\n"} for i in range(3)
    ]
    indexer.index_many("ws", "u1", notes)
    for i in range(3):
        assert len(repo.get_chunks(f"n{i}")) >= 1
        with Session(database.engine) as session:
            assert session.get(Note, f"n{i}").index_state == "indexed"


def test_index_many_one_failure_does_not_abort_batch(database):
    _seed_notes(database, 3)
    repo = NoteChunkRepository(database.engine)
    indexer = _indexer(database)
    # n9 has no Note row → replace_chunks UPDATE touches nothing but the chunk INSERT
    # FK to notes.id will fail for n9; the batch must still index n0 and n2.
    notes = [
        {"id": "n0", "title": "T0", "content": "# T0\n\nbody\n"},
        {"id": "n9", "title": "missing", "content": "# X\n\nbody\n"},
        {"id": "n2", "title": "T2", "content": "# T2\n\nbody\n"},
    ]
    indexer.index_many("ws", "u1", notes)  # must NOT raise
    assert len(repo.get_chunks("n0")) >= 1
    assert len(repo.get_chunks("n2")) >= 1
