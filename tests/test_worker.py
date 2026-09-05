import threading
import time

from sqlmodel import Session

from kajet_turbo.db import Database
from kajet_turbo.models import Job
from kajet_turbo.perf import record
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.worker import Handler, run_job, run_worker
from tests.helpers import entries_named, read_log_entries


def _run_worker_thread(engine, *, registry, poll_interval, concurrency, stop_event):
    t = threading.Thread(
        target=run_worker,
        args=(engine,),
        kwargs={
            "worker_id": "test-worker",
            "registry": registry,
            "poll_interval": poll_interval,
            "concurrency": concurrency,
            "stop_event": stop_event,
        },
    )
    t.start()
    return t


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
    t = _run_worker_thread(
        database.engine, registry=registry, poll_interval=0.02, concurrency=2, stop_event=stop
    )
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
    t = _run_worker_thread(
        database.engine,
        registry={"k": blocker},
        poll_interval=0.02,
        concurrency=1,
        stop_event=stop,
    )
    assert started.wait(timeout=5.0), "job did not start"
    stop.set()  # request shutdown while the job is in-flight
    release.set()  # let the in-flight job finish
    t.join(timeout=5.0)
    assert not t.is_alive()
    # graceful drain waited for the in-flight job -> it completed, none left running
    assert _get_required(database.engine, job_id).status == "done"


def test_run_worker_drains_burst_without_waiting_full_poll_interval(database: Database):
    """A saturated pool must wake as soon as a slot frees, not sleep a fixed tick.
    With poll_interval=5.0, concurrency=2, and 10 no-op jobs, the old loop's floor
    was (ceil(10/2) - 1) * 5s = 20s; the fix should drain in well under a second."""
    repo = JobRepository(database.engine)
    n = 10
    lock = threading.Lock()
    count = 0
    drained = threading.Event()

    def handler(_payload: dict) -> None:
        nonlocal count
        with lock:
            count += 1
            if count == n:
                drained.set()

    for _ in range(n):
        repo.enqueue("k", {}, now=0.0)

    stop = threading.Event()
    t = _run_worker_thread(
        database.engine,
        registry={"k": handler},
        poll_interval=5.0,
        concurrency=2,
        stop_event=stop,
    )
    try:
        assert drained.wait(timeout=3.0), "burst did not drain quickly"
    finally:
        stop.set()
        t.join(timeout=5.0)
    assert not t.is_alive()


def test_run_worker_tail_wakes_on_completion_not_poll_tick(database: Database):
    """A burst that drains to fewer runnable jobs than `concurrency` (claim() returns
    None while jobs are still inflight) must wake as soon as those jobs finish, not
    sleep a full poll_interval. With poll_interval=5.0 and 2 jobs at concurrency=4,
    the old loop's floor was one full poll_interval; the fix should drain in well
    under a second."""
    repo = JobRepository(database.engine)
    n = 2
    lock = threading.Lock()
    count = 0
    drained = threading.Event()

    def handler(_payload: dict) -> None:
        nonlocal count
        with lock:
            count += 1
            if count == n:
                drained.set()

    for _ in range(n):
        repo.enqueue("k", {}, now=0.0)

    stop = threading.Event()
    t = _run_worker_thread(
        database.engine,
        registry={"k": handler},
        poll_interval=5.0,
        concurrency=4,
        stop_event=stop,
    )
    try:
        assert drained.wait(timeout=3.0), "tail jobs did not drain quickly"
    finally:
        stop.set()
        t.join(timeout=5.0)
    assert not t.is_alive()


def test_run_worker_idle_backoff_does_not_spin(database: Database, monkeypatch):
    """Guards against regressing back to unconditional looping: an idle worker
    (empty queue) must still back off by poll_interval between claim attempts
    rather than spinning the DB."""
    calls = 0
    lock = threading.Lock()
    real_claim = JobRepository.claim

    def counting_claim(self, *args, **kwargs):
        nonlocal calls
        with lock:
            calls += 1
        return real_claim(self, *args, **kwargs)

    monkeypatch.setattr(JobRepository, "claim", counting_claim)

    stop = threading.Event()
    t = _run_worker_thread(
        database.engine, registry={}, poll_interval=0.05, concurrency=2, stop_event=stop
    )
    time.sleep(0.5)
    stop.set()
    t.join(timeout=5.0)
    assert not t.is_alive()
    # ~0.5s / 0.05s poll_interval => ~10 idle claims; a spin would produce thousands.
    assert calls < 30
