import json

import pytest
from sqlmodel import Session, select

from kajet_turbo.db import Database
from kajet_turbo.models import Job
from kajet_turbo.repositories.jobs import JobRepository, backoff_seconds


def _get(engine, job_id: str) -> Job:
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
