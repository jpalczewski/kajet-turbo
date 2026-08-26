import threading

from sqlmodel import Session

from kajet_turbo.db import Database
from kajet_turbo.models import Job
from kajet_turbo.perf import record
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.worker import Handler, run_job, run_worker
from tests.helpers import entries_named, read_log_entries


def _get(engine, job_id: str) -> Job | None:
    with Session(engine) as session:
        return session.get(Job, job_id)


def _get_required(engine, job_id: str) -> Job:
    job = _get(engine, job_id)
    assert job is not None
    return job


def test_run_job_success_completes(database: Database):
    repo = JobRepository(database.engine)
    seen: list[dict] = []

    def record(payload: dict) -> None:
        seen.append(payload)

    registry: dict[str, Handler] = {"k": record}
    job_id = repo.enqueue("k", {"v": 1}, now=1000.0)
    job = repo.claim("w", now=1000.0)
    assert job is not None
    run_job(repo, job, registry)
    assert seen == [{"v": 1}]
    assert _get_required(database.engine, job_id).status == "done"


def test_run_job_unknown_kind_terminal_fail(database: Database):
    repo = JobRepository(database.engine)
    job_id = repo.enqueue("mystery", {}, now=1000.0)
    job = repo.claim("w", now=1000.0)
    assert job is not None
    run_job(repo, job, registry={})
    row = _get_required(database.engine, job_id)
    assert row.status == "failed"
    assert row.last_error is not None
    assert "no handler" in row.last_error and "mystery" in row.last_error


def test_run_job_handler_exception_retries(database: Database):
    repo = JobRepository(database.engine)

    def boom(_payload: dict) -> None:
        raise RuntimeError("kaboom")

    job_id = repo.enqueue("k", {}, max_attempts=3, now=1000.0)
    job = repo.claim("w", now=1000.0)
    assert job is not None
    run_job(repo, job, registry={"k": boom})
    row = _get_required(database.engine, job_id)
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.last_error is not None
    assert "kaboom" in row.last_error


def test_run_job_logs_aggregate_db_and_git_timings(database: Database, capsys):
    from kajet_turbo.log import setup_logging

    setup_logging()
    repo = JobRepository(database.engine)
    job_id = repo.enqueue("k", {}, now=1000.0)
    job = repo.claim("w", now=1000.0)
    assert job is not None
    capsys.readouterr()  # discard enqueue/claim repository records

    def handler(_payload: dict) -> None:
        record("git_ms", 12.5)

    run_job(repo, job, {"k": handler})

    (entry,) = entries_named(read_log_entries(capsys), "job_finished")
    assert entry["job_id"] == job_id
    assert entry["kind"] == "k"
    assert entry["outcome"] == "completed"
    assert entry["duration_ms"] >= 0
    assert entry["db_ms"] >= 0
    assert entry["git_ms"] == 12.5


def test_run_job_logs_retry_outcome(database: Database, capsys):
    from kajet_turbo.log import setup_logging

    setup_logging()
    repo = JobRepository(database.engine)
    repo.enqueue("k", {}, max_attempts=2, now=1000.0)
    job = repo.claim("w", now=1000.0)
    assert job is not None
    capsys.readouterr()

    def handler(_payload: dict) -> None:
        raise RuntimeError("boom")

    run_job(repo, job, {"k": handler})

    (entry,) = entries_named(read_log_entries(capsys), "job_finished")
    assert entry["outcome"] == "retrying"
    assert entry["db_ms"] >= 0


def test_run_worker_processes_enqueued_job(database: Database):
    repo = JobRepository(database.engine)
    ran = threading.Event()
    registry = {"k": lambda _p: ran.set()}
    job_id = repo.enqueue("k", {}, now=0.0)  # next_run_at in the past -> immediately runnable
    stop = threading.Event()
    t = threading.Thread(
        target=run_worker,
        args=(database.engine,),
        kwargs={
            "worker_id": "test-worker",
            "registry": registry,
            "poll_interval": 0.02,
            "concurrency": 2,
            "stop_event": stop,
        },
    )
    t.start()
    assert ran.wait(timeout=5.0), "handler did not run"
    stop.set()
    t.join(timeout=5.0)
    assert not t.is_alive()
    assert _get_required(database.engine, job_id).status == "done"


def test_run_worker_graceful_drains_inflight(database: Database):
    repo = JobRepository(database.engine)
    started = threading.Event()
    release = threading.Event()

    def blocker(_payload):
        started.set()
        release.wait(timeout=5.0)

    job_id = repo.enqueue("k", {}, now=0.0)
    stop = threading.Event()
    t = threading.Thread(
        target=run_worker,
        args=(database.engine,),
        kwargs={
            "worker_id": "test-worker",
            "registry": {"k": blocker},
            "poll_interval": 0.02,
            "concurrency": 1,
            "stop_event": stop,
        },
    )
    t.start()
    assert started.wait(timeout=5.0), "job did not start"
    stop.set()  # request shutdown while the job is in-flight
    release.set()  # let the in-flight job finish
    t.join(timeout=5.0)
    assert not t.is_alive()
    # graceful drain waited for the in-flight job -> it completed, none left running
    assert _get_required(database.engine, job_id).status == "done"
