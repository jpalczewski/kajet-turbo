import json

from sqlmodel import Session

from kajet_turbo.embedding.cache import EmbeddingCacheRepository
from kajet_turbo.models import Note, User
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.notes import NoteChunkRepository
from kajet_turbo.services.indexing import NoteIndexer


def _indexer(database):
    repo = NoteChunkRepository(database.engine)
    cache = EmbeddingCacheRepository(database.engine)
    jobs = JobRepository(database.engine)
    indexer = NoteIndexer(repo, cache, lambda o: None, jobs=jobs)
    return indexer, jobs


def _seed_notes(database, n=3):
    with Session(database.engine) as session:
        session.add(User(id="u1", email="u1@test.com", created_at="2026-01-01"))
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


def test_index_many_enqueues_one_reindex_note_job_per_note(database):
    _seed_notes(database, 3)
    indexer, jobs = _indexer(database)
    notes = [{"id": f"n{i}"} for i in range(3)]

    indexer.index_many("ws", "u1", notes)

    pending = jobs.list_jobs("u1", status="pending", kind="reindex_note")
    assert sorted(json.loads(j.payload)["note_id"] for j in pending) == ["n0", "n1", "n2"]
    for job in pending:
        payload = json.loads(job.payload)
        assert payload["workspace"] == "ws"
        assert payload["owner_id"] == "u1"


def test_index_many_empty_batch_enqueues_nothing(database):
    indexer, jobs = _indexer(database)
    indexer.index_many("ws", "u1", [])
    assert jobs.list_jobs("u1", status="pending", kind="reindex_note") == []


def test_index_many_swallows_enqueue_failure(database, monkeypatch):
    # Best-effort contract (module docstring): index_many must never raise to callers that
    # already committed the note rows via defer_workspace_postprocess, even when the
    # enqueue itself blows up (e.g. a transient DB error).
    _seed_notes(database, 1)
    indexer, jobs = _indexer(database)

    def boom(*_args, **_kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(jobs, "enqueue_many", boom)

    indexer.index_many("ws", "u1", [{"id": "n0"}])  # must not raise


def test_index_many_dedupes_repeated_note_into_one_pending_job(database):
    # A second batch covering the same note before the first job runs must not create a
    # second pending row — same debounce the single-note enqueue_embed path relies on.
    _seed_notes(database, 1)
    indexer, jobs = _indexer(database)

    indexer.index_many("ws", "u1", [{"id": "n0"}])
    indexer.index_many("ws", "u1", [{"id": "n0"}])

    pending = jobs.list_jobs("u1", status="pending", kind="reindex_note")
    assert len(pending) == 1
