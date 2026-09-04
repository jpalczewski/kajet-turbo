from sqlmodel import Session

from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.cache import EmbeddingCacheRepository
from kajet_turbo.models import Note
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.notes import NoteChunkRepository
from kajet_turbo.services.indexing import NoteIndexer


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


def _cfg(api_key="k"):
    return EmbedderConfig(
        backend_id="fake", type="fake", model="fake-m", dim=3, base_url="http://x", api_key=api_key
    )


def _indexer(database, *, cfg=None):
    """Indexer with a recording enqueue fake — embedding is deferred to the job queue,
    so the unit under test here is chunk persistence + the enqueue decision."""
    repo = NoteChunkRepository(database.engine)
    cache = EmbeddingCacheRepository(database.engine)
    enqueued: list[tuple[str, str, str]] = []
    indexer = NoteIndexer(
        repo=repo,
        cache=cache,
        resolve_backend=lambda owner_id: cfg,
        jobs=JobRepository(database.engine),
        enqueue_embed=lambda nid, ws, owner: enqueued.append((nid, ws, owner)),
    )
    return indexer, repo, enqueued


def _index_state(database, note_id="n1") -> str:
    with Session(database.engine) as session:
        note = session.get(Note, note_id)
        assert note is not None
        return note.index_state


def test_index_note_writes_chunks_stale_and_enqueues(database):
    _note(database)
    indexer, repo, enqueued = _indexer(database, cfg=_cfg())
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nhello world\n\n## S\n\nmore text here\n")
    assert len(repo.get_chunks("n1")) >= 1
    assert _index_state(database) == "stale"  # vectors arrive out-of-band via the worker
    assert enqueued == [("n1", "ws", "u1")]


def test_index_note_resolver_error_degrades_to_stale(database):
    # resolve_backend raising (e.g. SECRET_KEY unset → cipher refuses) must not lose chunks.
    _note(database)
    repo = NoteChunkRepository(database.engine)
    cache = EmbeddingCacheRepository(database.engine)
    enqueued: list = []

    def _boom(owner_id):
        raise ValueError("SECRET_KEY must be set")

    indexer = NoteIndexer(
        repo=repo,
        cache=cache,
        resolve_backend=_boom,
        jobs=JobRepository(database.engine),
        enqueue_embed=lambda *a: enqueued.append(a),
    )
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nbody\n")
    assert len(repo.get_chunks("n1")) >= 1
    assert _index_state(database) == "stale"
    assert enqueued == []


def test_index_note_no_backend_writes_chunks_and_skips_enqueue(database):
    _note(database)
    indexer, repo, enqueued = _indexer(database, cfg=None)
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nbody\n")
    assert len(repo.get_chunks("n1")) >= 1
    assert _index_state(database) == "stale"
    assert enqueued == []


def test_index_note_keyless_profile_enqueues(database):
    # A keyless profile is a valid local/no-auth endpoint: it MUST still enqueue embedding
    # (the adapter omits the Authorization header), not silently stay FTS-only.
    _note(database)
    indexer, _repo, enqueued = _indexer(database, cfg=_cfg(api_key=None))
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nbody\n")
    assert enqueued == [("n1", "ws", "u1")]


def test_index_note_without_enqueue_callable_still_indexes(database):
    _note(database)
    repo = NoteChunkRepository(database.engine)
    indexer = NoteIndexer(
        repo=repo,
        cache=EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda owner_id: _cfg(),
        jobs=JobRepository(database.engine),
    )
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nbody\n")  # must not raise
    assert len(repo.get_chunks("n1")) >= 1


def test_index_note_empty_content_clears_chunks_and_skips_enqueue(database):
    _note(database)
    indexer, repo, enqueued = _indexer(database, cfg=_cfg())
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nbody\n")
    indexer.index_note("n1", "ws", "u1", "T", "   \n\n  ")
    assert repo.get_chunks("n1") == []
    assert enqueued == [("n1", "ws", "u1")]  # only the first, chunk-producing index
