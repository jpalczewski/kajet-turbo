"""Server-level job-queue wiring: sweep handler, handler registration, and the
in-process worker thread for the combined (role=all) app."""

import time

from sqlmodel import Session, col, select

from kajet_turbo.db import Database
from kajet_turbo.models import Job, Note, NoteShareLinkVisit
from kajet_turbo.repositories.events import EventRepository
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.note_share_link import NoteShareLinkRepository
from kajet_turbo.server import _make_sweep_handler, register_job_handlers


def test_sweep_handler_purges_old_done_jobs(database: Database):
    events = EventRepository(database.engine)
    jobs = JobRepository(database.engine)
    share_links = NoteShareLinkRepository(database.engine)
    old_done = jobs.enqueue("k", {}, now=1000.0)
    jobs.claim("w1", now=1000.0)
    jobs.complete(old_done, "w1", now=1000.0)  # updated_at in 1970 → far older than 24h

    _make_sweep_handler(events, jobs, share_links)({})

    with Session(database.engine) as session:
        assert session.get(Job, old_done) is None
        rearmed = session.exec(select(Job).where(Job.kind == "sweep_outbox")).all()
    assert len(rearmed) == 1 and rearmed[0].status == "pending"


def test_sweep_handler_purges_expired_share_link_visits(database: Database):
    from tests.conftest import seed_user

    seed_user(database, "u1")
    with Session(database.engine) as session:
        session.add(
            Note(
                id="n1",
                workspace="ws",
                owner_id="u1",
                title="Shared",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
        )
        session.commit()
    share_links = NoteShareLinkRepository(database.engine)
    link = share_links.create("n1", "ws", "u1")
    share_links.record_visit(link.token, "203.0.113.1", "Old browser")
    share_links.record_visit(link.token, "203.0.113.2", "Fresh browser")
    with Session(database.engine) as session:
        visit = session.exec(
            select(NoteShareLinkVisit).order_by(col(NoteShareLinkVisit.id))
        ).first()
        assert visit is not None
        visit.created_at = "1970-01-01T00:00:00+00:00"
        session.add(visit)
        session.commit()

    _make_sweep_handler(
        EventRepository(database.engine), JobRepository(database.engine), share_links
    )({})

    with Session(database.engine) as session:
        visits = session.exec(select(NoteShareLinkVisit)).all()
    assert len(visits) == 1
    assert visits[0].user_agent == "Fresh browser"


def test_register_job_handlers_covers_all_kinds(database: Database):
    from kajet_turbo.dependencies import AppConfig, build_resources

    resources = build_resources(
        AppConfig(db_path=database.db_path, mcp_base_url="http://localhost")
    )
    handlers = register_job_handlers(resources)
    for kind in (
        "push_workspace",
        "reconcile_links",
        "heal_dangling",
        "sweep_outbox",
        "embed_note",
    ):
        assert handlers[kind] is not None, kind
    resources.db.close()


def test_build_app_runs_inprocess_worker():
    # Role "all" (bare local dev) must drain the queue itself — otherwise deferred
    # embeddings (and auto-push) would silently never happen without a worker process.
    from starlette.testclient import TestClient

    from kajet_turbo.server import build_app

    app = build_app()
    resources = app._app.state.resources
    # Handler no-ops for a nonexistent note, so the job completes quietly.
    job_id = resources.job_repo.enqueue(
        "embed_note", {"note_id": "missing", "workspace": "w", "owner_id": "u"}
    )

    def _status() -> str:
        with Session(resources.db.engine) as session:
            job = session.get(Job, job_id)
            assert job is not None
            return job.status

    with TestClient(app):
        deadline = time.time() + 10.0
        while time.time() < deadline and _status() != "done":
            time.sleep(0.05)
    assert _status() == "done"
