import json
import threading

import pytest
from sqlmodel import Session, select

from kajet_turbo.db import Database
from kajet_turbo.models import Job, User
from kajet_turbo.repositories.jobs import JobRepository, backoff_seconds


def _get(engine, job_id: str) -> Job | None:
    with Session(engine) as session:
        return session.get(Job, job_id)


def _pending(engine, kind: str, dedup_key: str) -> list[Job]:
    with Session(engine) as session:
        return list(
            session.execute(
                select(Job).where(
                    Job.kind == kind,
                    Job.dedup_key == dedup_key,
                    Job.status == "pending",
                )
            )
            .scalars()
            .all()
        )


def _ensure_user(engine, user_id: str, email: str = "") -> None:
    """Create a user if it doesn't exist."""
    with Session(engine) as session:
        if session.get(User, user_id) is None:
            user = User(
                id=user_id,
                email=email or f"{user_id}@test.local",
                created_at="2026-01-01T00:00:00+00:00",
            )
            session.add(user)
            session.commit()


def test_enqueue_creates_pending_job(database: Database):
    repo = JobRepository(database.engine)
    job_id = repo.enqueue("push_workspace", {"workspace": "ws1"}, now=1000.0)
    job = _get(database.engine, job_id)
    assert job.status == "pending"
    assert job.attempts == 0
    assert json.loads(job.payload) == {"workspace": "ws1"}
    assert job.next_run_at == 1000.0


def test_enqueue_without_dedup_always_inserts(database: Database):
    repo = JobRepository(database.engine)
    a = repo.enqueue("k", {"n": 1})
    b = repo.enqueue("k", {"n": 2})
    assert a != b


def test_enqueue_with_dedup_rearms_single_pending(database: Database):
    repo = JobRepository(database.engine)
    first = repo.enqueue("push_workspace", {"v": 1}, dedup_key="u:ws", now=1000.0)
    second = repo.enqueue("push_workspace", {"v": 2}, dedup_key="u:ws", now=1005.0)
    assert first == second  # same pending row re-armed, not duplicated
    rows = _pending(database.engine, "push_workspace", "u:ws")
    assert len(rows) == 1
    assert rows[0].next_run_at == 1005.0  # re-armed to the later time


def test_enqueue_dedup_distinct_keys_coexist(database: Database):
    repo = JobRepository(database.engine)
    a = repo.enqueue("push_workspace", {}, dedup_key="u:ws1")
    b = repo.enqueue("push_workspace", {}, dedup_key="u:ws2")
    assert a != b


def test_enqueue_delay_sets_future_next_run_at(database: Database):
    repo = JobRepository(database.engine)
    job_id = repo.enqueue("k", {}, delay=30.0, now=1000.0)
    assert _get(database.engine, job_id).next_run_at == 1030.0


@pytest.mark.parametrize(
    "attempts,expected",
    [(1, 2.0), (2, 4.0), (3, 8.0), (10, 300.0)],
)
def test_backoff_seconds(attempts, expected):
    assert backoff_seconds(attempts) == expected


def test_claim_returns_and_locks_pending(database: Database):
    repo = JobRepository(database.engine)
    job_id = repo.enqueue("k", {"x": 1}, now=1000.0)
    claimed = repo.claim("worker-a", now=1001.0)
    assert claimed is not None and claimed.id == job_id
    row = _get(database.engine, job_id)
    assert row.status == "running"
    assert row.locked_by == "worker-a"
    assert row.locked_at == 1001.0
    # nothing else runnable now
    assert repo.claim("worker-a", now=1002.0) is None


def test_claim_skips_future_next_run_at(database: Database):
    repo = JobRepository(database.engine)
    repo.enqueue("k", {}, delay=60.0, now=1000.0)
    assert repo.claim("worker-a", now=1000.0) is None
    assert repo.claim("worker-a", now=1061.0) is not None


def test_claim_reclaims_stale_running_job(database: Database):
    repo = JobRepository(database.engine)
    job_id = repo.enqueue("k", {}, now=1000.0)
    repo.claim("dead-worker", now=1000.0)  # now running, locked_at=1000
    # not stale yet
    assert repo.claim("worker-b", now=1100.0, stale_after=300.0) is None
    # stale: locked_at 1000 < (1400 - 300)=1100
    reclaimed = repo.claim("worker-b", now=1400.0, stale_after=300.0)
    assert reclaimed is not None and reclaimed.id == job_id
    assert _get(database.engine, job_id).locked_by == "worker-b"


def test_claim_no_double_claim_under_concurrency(database: Database):
    repo = JobRepository(database.engine)
    repo.enqueue("k", {}, now=1000.0)
    barrier = threading.Barrier(2)
    results: list[Job | None] = []
    lock = threading.Lock()

    def worker(name: str):
        barrier.wait()
        got = repo.claim(name, now=1001.0)
        with lock:
            results.append(got)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    non_none = [r for r in results if r is not None]
    assert len(non_none) == 1  # exactly one worker claimed the single job


def test_complete_marks_done(database: Database):
    repo = JobRepository(database.engine)
    job_id = repo.enqueue("k", {}, now=1000.0)
    repo.claim("w", now=1000.0)
    repo.complete(job_id, now=1002.0)
    assert _get(database.engine, job_id).status == "done"


def test_fail_retries_with_backoff_then_terminal(database: Database):
    repo = JobRepository(database.engine)
    job_id = repo.enqueue("k", {}, max_attempts=2, now=1000.0)
    repo.claim("w", now=1000.0)
    repo.fail(job_id, "boom", now=1000.0)
    row = _get(database.engine, job_id)
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.next_run_at == 1002.0  # now + backoff(1)=2.0
    assert row.locked_by is None
    assert row.last_error == "boom"
    # second failure reaches max_attempts -> failed
    repo.claim("w", now=1002.0)
    repo.fail(job_id, "boom2", now=1002.0)
    row = _get(database.engine, job_id)
    assert row.status == "failed"
    assert row.attempts == 2


def test_fail_terminal_fails_immediately(database: Database):
    repo = JobRepository(database.engine)
    job_id = repo.enqueue("k", {}, max_attempts=5, now=1000.0)
    repo.claim("w", now=1000.0)
    repo.fail_terminal(job_id, "no handler for kind 'k'", now=1001.0)
    row = _get(database.engine, job_id)
    assert row.status == "failed"
    assert row.last_error == "no handler for kind 'k'"


def test_reset_running_to_pending_scopes_to_worker(database: Database):
    repo = JobRepository(database.engine)
    a = repo.enqueue("k", {}, dedup_key="a", now=1000.0)
    b = repo.enqueue("k", {}, dedup_key="b", now=1000.0)
    repo.claim("worker-a", now=1000.0)  # claims one (lowest next_run_at / first)
    repo.claim("worker-b", now=1000.0)  # claims the other
    n = repo.reset_running_to_pending("worker-a", now=1100.0)
    assert n == 1
    # worker-a's job is pending again; worker-b's is still running
    statuses = {_get(database.engine, a).status, _get(database.engine, b).status}
    assert statuses == {"pending", "running"}


def _make_failed(repo: JobRepository, engine, *, user_id: str, now: float = 1000.0) -> str:
    job_id = repo.enqueue("k", {}, user_id=user_id, dedup_key=None, max_attempts=1, now=now)
    repo.claim("w", now=now)
    repo.fail(job_id, "boom", now=now)  # max_attempts=1 -> failed
    return job_id


def test_list_jobs_scoped_to_user_newest_first(database: Database):
    repo = JobRepository(database.engine)
    _ensure_user(database.engine, "u1")
    _ensure_user(database.engine, "u2")
    repo.enqueue("k", {"n": 1}, user_id="u1", now=1000.0)
    repo.enqueue("k", {"n": 2}, user_id="u1", now=1001.0)
    repo.enqueue("k", {"n": 9}, user_id="u2", now=1002.0)
    jobs = repo.list_jobs("u1")
    assert [j.user_id for j in jobs] == ["u1", "u1"]
    assert jobs[0].created_at >= jobs[1].created_at  # newest first


def test_list_jobs_filters_status_and_kind(database: Database):
    repo = JobRepository(database.engine)
    _ensure_user(database.engine, "u1")
    repo.enqueue("push", {}, user_id="u1", dedup_key="a", now=1000.0)
    failed = _make_failed(repo, database.engine, user_id="u1", now=1001.0)
    assert {j.id for j in repo.list_jobs("u1", status="failed")} == {failed}
    assert {j.kind for j in repo.list_jobs("u1", kind="push")} == {"push"}


def test_list_jobs_pagination(database: Database):
    repo = JobRepository(database.engine)
    _ensure_user(database.engine, "u1")
    for i in range(3):
        repo.enqueue("k", {"i": i}, user_id="u1", dedup_key=f"d{i}", now=1000.0 + i)
    page = repo.list_jobs("u1", limit=1, offset=1)
    assert len(page) == 1


def test_retry_rearms_only_failed_owned_jobs(database: Database):
    repo = JobRepository(database.engine)
    _ensure_user(database.engine, "u1")
    _ensure_user(database.engine, "u2")
    failed = _make_failed(repo, database.engine, user_id="u1", now=1000.0)
    assert repo.retry(failed, "u2", now=2000.0) is False  # not owner
    assert repo.retry(failed, "u1", now=2000.0) is True
    row = _get(database.engine, failed)
    assert row.status == "pending" and row.attempts == 0 and row.last_error is None
    assert repo.retry(failed, "u1", now=2000.0) is False  # now pending, not failed


def test_dismiss_deletes_only_terminal_owned_jobs(database: Database):
    repo = JobRepository(database.engine)
    _ensure_user(database.engine, "u1")
    _ensure_user(database.engine, "u2")
    pending = repo.enqueue("k", {}, user_id="u1", dedup_key="p", now=1000.0)
    assert repo.dismiss(pending, "u1") is False  # not terminal
    failed = _make_failed(repo, database.engine, user_id="u1", now=1001.0)
    assert repo.dismiss(failed, "u2") is False  # not owner
    assert repo.dismiss(failed, "u1") is True
    assert _get(database.engine, failed) is None
