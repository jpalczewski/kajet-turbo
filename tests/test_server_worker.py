"""Server-level job-queue wiring: sweep handler, handler registration, and the
in-process worker thread for the combined (role=all) app."""

import time

from sqlmodel import Session, select

from kajet_turbo.db import Database
from kajet_turbo.models import Job
from kajet_turbo.repositories.events import EventRepository
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.server import _make_sweep_handler, register_job_handlers
from kajet_turbo.worker import get_handler


def test_sweep_handler_purges_old_done_jobs(database: Database):
    events = EventRepository(database.engine)
    jobs = JobRepository(database.engine)
    old_done = jobs.enqueue("k", {}, now=1000.0)
    jobs.claim("w1", now=1000.0)
    jobs.complete(old_done, now=1000.0)  # updated_at in 1970 → far older than 24h

    _make_sweep_handler(events, jobs)({})

    with Session(database.engine) as session:
        assert session.get(Job, old_done) is None
        rearmed = session.exec(select(Job).where(Job.kind == "sweep_outbox")).all()
    assert len(rearmed) == 1 and rearmed[0].status == "pending"


def test_register_job_handlers_covers_all_kinds():
    register_job_handlers()
    for kind in (
        "push_workspace",
        "reconcile_links",
        "heal_dangling",
        "sweep_outbox",
        "embed_note",
    ):
        assert get_handler(kind) is not None, kind


def test_build_app_runs_inprocess_worker():
    # Role "all" (bare local dev) must drain the queue itself — otherwise deferred
    # embeddings (and auto-push) would silently never happen without a worker process.
    from starlette.testclient import TestClient

    from kajet_turbo import dependencies
    from kajet_turbo.server import build_app

    # Handler no-ops for a nonexistent note, so the job completes quietly.
    job_id = dependencies.job_repo.enqueue(
        "embed_note", {"note_id": "missing", "workspace": "w", "owner_id": "u"}
    )

    def _status() -> str:
        with Session(dependencies.db.engine) as session:
            job = session.get(Job, job_id)
            assert job is not None
            return job.status

    with TestClient(build_app()):
        deadline = time.time() + 10.0
        while time.time() < deadline and _status() != "done":
            time.sleep(0.05)
    assert _status() == "done"
