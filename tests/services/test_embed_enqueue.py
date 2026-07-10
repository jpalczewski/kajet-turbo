import json

from sqlmodel import Session

from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.cache import EmbeddingCacheRepository
from kajet_turbo.models import Note, User
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.notes import NoteChunkRepository
from kajet_turbo.services.embed_enqueue import make_enqueue_embed
from kajet_turbo.services.indexing import NoteIndexer


def _cfg():
    return EmbedderConfig(
        backend_id="fake", type="fake", model="fake-m", dim=3, base_url="http://x", api_key="k"
    )


def _seed(database, note_ids=("n1",)):
    with Session(database.engine) as session:
        session.add(User(id="u1", email="u@e.com", created_at="2026-01-01"))
        for note_id in note_ids:
            session.add(
                Note(
                    id=note_id,
                    workspace="ws",
                    owner_id="u1",
                    title="T",
                    created_at="2026-01-01",
                    updated_at="2026-01-01",
                )
            )
        session.commit()


def _indexer(database, jobs, *, cfg=None):
    return NoteIndexer(
        repo=NoteChunkRepository(database.engine),
        cache=EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda owner_id: cfg,
        enqueue_embed=make_enqueue_embed(jobs),
    )


def test_index_note_enqueues_embed_job(database):
    _seed(database)
    jobs = JobRepository(database.engine)
    indexer = _indexer(database, jobs, cfg=_cfg())

    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nbody\n")

    listed = jobs.list_jobs("u1", kind="embed_note")
    assert len(listed) == 1
    job = listed[0]
    assert job.status == "pending"
    assert job.dedup_key == "u1:ws:n1"
    assert json.loads(job.payload) == {"note_id": "n1", "workspace": "ws", "owner_id": "u1"}
    with Session(database.engine) as session:
        note = session.get(Note, "n1")
        assert note is not None
        assert note.index_state == "stale"  # vectors arrive out-of-band via the worker


def test_rapid_edits_coalesce_to_one_pending_job(database):
    _seed(database)
    jobs = JobRepository(database.engine)
    indexer = _indexer(database, jobs, cfg=_cfg())

    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nfirst\n")
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nsecond\n")

    assert len(jobs.list_jobs("u1", kind="embed_note", status="pending")) == 1


def test_no_backend_enqueues_nothing(database):
    _seed(database)
    jobs = JobRepository(database.engine)
    indexer = _indexer(database, jobs, cfg=None)
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nbody\n")
    assert jobs.list_jobs("u1", kind="embed_note") == []


def test_resolver_error_enqueues_nothing_but_writes_chunks(database):
    _seed(database)
    jobs = JobRepository(database.engine)
    repo = NoteChunkRepository(database.engine)

    def _boom(owner_id):
        raise ValueError("SECRET_KEY must be set")

    indexer = NoteIndexer(
        repo=repo,
        cache=EmbeddingCacheRepository(database.engine),
        resolve_backend=_boom,
        enqueue_embed=make_enqueue_embed(jobs),
    )
    indexer.index_note("n1", "ws", "u1", "T", "# T\n\nbody\n")
    assert len(repo.get_chunks("n1")) >= 1
    assert jobs.list_jobs("u1", kind="embed_note") == []


def test_empty_content_enqueues_nothing(database):
    _seed(database)
    jobs = JobRepository(database.engine)
    indexer = _indexer(database, jobs, cfg=_cfg())
    indexer.index_note("n1", "ws", "u1", "T", "   \n\n  ")
    assert jobs.list_jobs("u1", kind="embed_note") == []


def test_index_many_enqueues_per_note(database):
    _seed(database, note_ids=("n0", "n1", "n2"))
    jobs = JobRepository(database.engine)
    indexer = _indexer(database, jobs, cfg=_cfg())

    notes = [
        {"id": f"n{i}", "title": f"T{i}", "content": f"# T{i}\n\nbody {i}\n"} for i in range(3)
    ]
    indexer.index_many("ws", "u1", notes)

    listed = jobs.list_jobs("u1", kind="embed_note", status="pending")
    assert sorted(j.dedup_key for j in listed) == ["u1:ws:n0", "u1:ws:n1", "u1:ws:n2"]
