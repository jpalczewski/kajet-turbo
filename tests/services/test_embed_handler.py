import pytest
from sqlalchemy import text as _text
from sqlmodel import Session

from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.cache import EmbeddingCacheRepository, content_hash
from kajet_turbo.markdown import Chunk, embedded_text
from kajet_turbo.models import Note
from kajet_turbo.repositories.notes import NoteChunkRepository
from kajet_turbo.services.embed_handler import EmbedNoteHandler


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


def _cfg():
    return EmbedderConfig(
        backend_id="fake", type="fake", model="fake-m", dim=3, base_url="http://x", api_key="k"
    )


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


PAYLOAD = {"note_id": "n1", "workspace": "ws", "owner_id": "u1"}


def _handler(database, *, cfg=None, embedder=None):
    repo = NoteChunkRepository(database.engine)
    cache = EmbeddingCacheRepository(database.engine)
    emb = embedder or _FakeEmbedder()
    handler = EmbedNoteHandler(
        chunk_repo=repo,
        cache=cache,
        resolve_backend=lambda owner_id: cfg,
        build_embedder=lambda c: emb,
    )
    return handler, repo, cache, emb


def _stale_note(database) -> NoteChunkRepository:
    _note(database)
    repo = NoteChunkRepository(database.engine)
    repo.replace_chunks("n1", "ws", "u1", "T", _chunks(), embeddings=None, dim=None)
    return repo


def _index_state(database, note_id="n1") -> str:
    with Session(database.engine) as session:
        note = session.get(Note, note_id)
        assert note is not None
        return note.index_state


def test_handler_embeds_stored_chunks_and_marks_indexed(database):
    _stale_note(database)
    handler, repo, _cache, emb = _handler(database, cfg=_cfg())

    handler(PAYLOAD)

    assert _index_state(database) == "indexed"
    assert len(emb.calls) == 1
    with Session(database.engine) as session:
        vec_count = session.execute(  # ty: ignore[deprecated] - raw SQL
            _text("SELECT COUNT(*) FROM note_chunks_vec_3 WHERE note_id='n1'")
        ).scalar_one()
    assert vec_count == 2
    meta = repo.get_index_meta("u1")
    assert meta == {"backend": "fake", "model": "fake-m", "dim": 3}


def test_handler_cache_hits_skip_embedder(database):
    _stale_note(database)
    handler, _repo, cache, emb = _handler(database, cfg=_cfg())
    hashes = {content_hash(embedded_text(c)): [1.0, 2.0, 3.0] for c in _chunks()}
    cache.put_many(hashes, "fake", "fake-m", 3)

    handler(PAYLOAD)

    assert emb.calls == []
    assert _index_state(database) == "indexed"


def test_handler_no_chunks_is_noop(database):
    # Note deleted (or never chunked) after enqueue: the job must complete quietly.
    handler, _repo, _cache, emb = _handler(database, cfg=_cfg())
    handler(PAYLOAD)
    assert emb.calls == []


def test_handler_no_backend_is_noop(database):
    _stale_note(database)
    handler, _repo, _cache, emb = _handler(database, cfg=None)
    handler(PAYLOAD)
    assert emb.calls == []
    assert _index_state(database) == "stale"


def test_handler_embedder_error_propagates_for_retry(database):
    _stale_note(database)

    class _Boom(_FakeEmbedder):
        async def embed_documents(self, texts):
            raise RuntimeError("API down")

    handler, _repo, _cache, _emb = _handler(database, cfg=_cfg(), embedder=_Boom())
    with pytest.raises(RuntimeError, match="API down"):
        handler(PAYLOAD)
    assert _index_state(database) == "stale"


def test_handler_superseded_by_concurrent_edit_completes_without_meta(database):
    repo = _stale_note(database)

    class _Racer(_FakeEmbedder):
        """Replaces the note's chunks mid-embed, simulating an edit landing between
        the handler's chunk read and its vector attach."""

        async def embed_documents(self, texts):
            new = [
                Chunk(ordinal=0, header_path=["# T"], content="edited", char_start=0, char_end=6)
            ]
            repo.replace_chunks("n1", "ws", "u1", "T", new, embeddings=None, dim=None)
            return await super().embed_documents(texts)

    handler, repo2, _cache, _emb = _handler(database, cfg=_cfg(), embedder=_Racer())
    handler(PAYLOAD)  # must not raise — the follow-up job repairs

    assert _index_state(database) == "stale"
    assert repo2.get_index_meta("u1") is None
