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

    def __init__(self):
        self.calls = []

    async def embed_documents(self, texts):
        self.calls.append(list(texts))
        return [[float(len(t)), 0.0, 1.0] for t in texts]

    async def embed_query(self, text):
        return [float(len(text)), 0.0, 1.0]


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


def _cfg():
    return EmbedderConfig(
        backend_id="fake", type="fake", model="fake-m", dim=3, base_url="http://x", api_key="k"
    )


def _indexer(database, *, cfg=None, embedder=None):
    repo = NoteChunkRepository(database.engine)
    cache = EmbeddingCacheRepository(database.engine)
    emb = embedder or _FakeEmbedder()
    return (
        NoteIndexer(
            repo=repo,
            cache=cache,
            resolve_backend=lambda owner_id: cfg,
            build_embedder=lambda c: emb,
        ),
        repo,
        emb,
    )


def test_index_note_embeds_and_marks_indexed(database):
    _note(database)
    indexer, repo, emb = _indexer(database, cfg=_cfg())
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nhello world\n\n## S\n\nmore text here\n")
    assert len(repo.get_chunks("n1")) >= 1
    with Session(database.engine) as session:
        assert session.get(Note, "n1").index_state == "indexed"
    assert len(emb.calls) == 1


def test_index_note_resolver_error_degrades_to_stale(database):
    # resolve_backend raising (e.g. SECRET_KEY unset → cipher refuses) must not lose chunks.
    _note(database)
    repo = NoteChunkRepository(database.engine)
    cache = EmbeddingCacheRepository(database.engine)

    def _boom(owner_id):
        raise ValueError("SECRET_KEY must be set")

    indexer = NoteIndexer(
        repo=repo, cache=cache, resolve_backend=_boom, build_embedder=lambda c: _FakeEmbedder()
    )
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nbody\n")
    assert len(repo.get_chunks("n1")) >= 1
    with Session(database.engine) as session:
        assert session.get(Note, "n1").index_state == "stale"


def test_index_note_uses_cache_to_skip_embedding(database):
    _note(database)
    indexer, _repo, emb = _indexer(database, cfg=_cfg())
    text_ = "# T\n\nrepeated body\n"
    indexer.index_note("n1", "ws", "u1", "T", text_)
    first = len(emb.calls)
    indexer.index_note("n1", "ws", "u1", "T", text_)
    assert len(emb.calls) == first  # identical → all cache hits, no new embed call


def test_index_note_no_backend_writes_chunks_stale(database):
    _note(database)
    indexer, repo, emb = _indexer(database, cfg=None)
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nbody\n")
    assert len(repo.get_chunks("n1")) >= 1
    with Session(database.engine) as session:
        assert session.get(Note, "n1").index_state == "stale"
    assert emb.calls == []


def test_index_note_keyless_profile_embeds(database):
    # A keyless profile is a valid local/no-auth endpoint: it MUST still embed (the adapter
    # omits the Authorization header), not silently degrade to FTS-only.
    _note(database)
    cfg = EmbedderConfig(
        backend_id="fake", type="fake", model="fake-m", dim=3, base_url="http://x", api_key=None
    )
    indexer, repo, emb = _indexer(database, cfg=cfg)
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nbody\n")
    assert len(repo.get_chunks("n1")) >= 1
    with Session(database.engine) as session:
        assert session.get(Note, "n1").index_state == "indexed"
    assert len(emb.calls) == 1  # embedder was called despite no api_key


def test_index_note_embedder_error_degrades_to_stale(database):
    _note(database)

    class _Boom(_FakeEmbedder):
        async def embed_documents(self, texts):
            raise RuntimeError("API down")

    indexer, repo, _emb = _indexer(database, cfg=_cfg(), embedder=_Boom())
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nbody\n")
    assert len(repo.get_chunks("n1")) >= 1
    with Session(database.engine) as session:
        assert session.get(Note, "n1").index_state == "stale"


def test_index_note_empty_content_clears_chunks(database):
    _note(database)
    indexer, repo, _emb = _indexer(database, cfg=_cfg())
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nbody\n")
    indexer.index_note("n1", "ws", "u1", "T", "   \n\n  ")
    assert repo.get_chunks("n1") == []


def test_clear_note_removes_chunks(database):
    _note(database)
    indexer, repo, _emb = _indexer(database, cfg=_cfg())
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nbody\n")
    indexer.clear_note("n1")
    assert repo.get_chunks("n1") == []
