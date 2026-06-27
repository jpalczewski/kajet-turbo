"""The job queue lives in SQLite. This repository is the only writer of `jobs`
rows: enqueue (with debounce), atomic claim, and lifecycle transitions. Time
values are epoch seconds; ``now`` is injectable so tests are deterministic."""

import json
import time

from nanoid import generate
from sqlalchemy import Engine, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, col, select

from kajet_turbo.models import Job


def backoff_seconds(attempts: int, base: float = 2.0, cap: float = 300.0) -> float:
    """Exponential backoff for the Nth retry (attempts >= 1), capped at ``cap``."""
    return min(cap, base * (2 ** (attempts - 1)))


# One atomic statement: pick the single most-overdue runnable row and lock it.
# Eligible = a pending job whose time has come, OR a running job whose worker died
# (locked_at older than the stale cutoff). SQLite serializes the write, so two
# workers never claim the same row. Additionally, a job is skipped while another
# RUNNING job shares its (non-NULL) dedup_key — this serializes same-key work (e.g.
# one push per workspace at a time) and coalesces a burst into one follow-up.
_CLAIM_SQL = text(
    """
    UPDATE jobs
    SET status='running', locked_by=:worker, locked_at=:now, updated_at=:now
    WHERE id = (
        SELECT j.id FROM jobs j
        WHERE (
            (j.status='pending' AND j.next_run_at <= :now)
            OR (j.status='running' AND j.locked_at IS NOT NULL AND j.locked_at < :stale_cutoff)
        )
        AND NOT EXISTS (
            SELECT 1 FROM jobs r
            WHERE r.status='running'
              AND r.dedup_key IS NOT NULL
              AND r.dedup_key = j.dedup_key
              AND r.id <> j.id
        )
        ORDER BY j.next_run_at
        LIMIT 1
    )
    RETURNING *
    """
)


class JobRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def enqueue(
        self,
        kind: str,
        payload: dict,
        *,
        dedup_key: str | None = None,
        user_id: str | None = None,
        max_attempts: int = 5,
        delay: float = 0.0,
        now: float | None = None,
    ) -> str:
        now = time.time() if now is None else now
        run_at = now + delay
        body = json.dumps(payload)
        job_id = generate()
        with Session(self._engine) as session:
            if dedup_key is None:
                session.add(
                    Job(
                        id=job_id,
                        kind=kind,
                        user_id=user_id,
                        dedup_key=None,
                        payload=body,
                        status="pending",
                        attempts=0,
                        max_attempts=max_attempts,
                        next_run_at=run_at,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.commit()
                return job_id

            # Debounce: one pending job per (kind, dedup_key). On conflict with the
            # partial unique index, re-arm the existing pending row instead of
            # inserting a duplicate.
            stmt = (
                sqlite_insert(Job)
                .values(
                    id=job_id,
                    kind=kind,
                    user_id=user_id,
                    dedup_key=dedup_key,
                    payload=body,
                    status="pending",
                    attempts=0,
                    max_attempts=max_attempts,
                    next_run_at=run_at,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[Job.kind, Job.dedup_key],  # ty: ignore[invalid-argument-type] — SQLAlchemy column descriptors satisfy DDLConstraintColumnRole at runtime; ty infers str|None from the model field annotation
                    index_where=(Job.status == "pending"),  # ty: ignore[invalid-argument-type] — ColumnElement.__eq__ returns ColumnElement[bool], not bool; ty loses the overload
                    set_={"next_run_at": run_at, "updated_at": now},
                )
            )
            session.execute(stmt)  # ty: ignore[deprecated] — sqlite INSERT ON CONFLICT requires execute(), not exec()
            session.commit()
            return session.execute(  # ty: ignore[deprecated] — raw SQL path for DML consistency
                select(Job.id).where(
                    Job.kind == kind,
                    Job.dedup_key == dedup_key,
                    Job.status == "pending",
                )
            ).scalar_one()

    def claim(
        self, worker_id: str, *, now: float | None = None, stale_after: float = 300.0
    ) -> Job | None:
        now = time.time() if now is None else now
        with Session(self._engine) as session:
            row = session.execute(  # ty: ignore[deprecated] - raw SQL
                _CLAIM_SQL,
                {"worker": worker_id, "now": now, "stale_cutoff": now - stale_after},
            ).fetchone()
            session.commit()
            return None if row is None else Job(**row._mapping)

    def complete(self, job_id: str, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text("UPDATE jobs SET status='done', updated_at=:now WHERE id=:id"),
                {"now": now, "id": job_id},
            )
            session.commit()

    def fail(
        self,
        job_id: str,
        error: str,
        *,
        now: float | None = None,
        base_backoff: float = 2.0,
        max_backoff: float = 300.0,
    ) -> None:
        now = time.time() if now is None else now
        with Session(self._engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.attempts += 1
            job.last_error = error
            job.updated_at = now
            if job.attempts >= job.max_attempts:
                job.status = "failed"
            else:
                job.status = "pending"
                job.next_run_at = now + backoff_seconds(job.attempts, base_backoff, max_backoff)
                job.locked_by = None
                job.locked_at = None
            session.add(job)
            session.commit()

    def fail_terminal(self, job_id: str, error: str, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "UPDATE jobs SET status='failed', last_error=:err, updated_at=:now WHERE id=:id"
                ),
                {"err": error, "now": now, "id": job_id},
            )
            session.commit()

    def reset_running_to_pending(self, worker_id: str, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with Session(self._engine) as session:
            result = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "UPDATE jobs SET status='pending', next_run_at=:now, locked_by=NULL, "
                    "locked_at=NULL, updated_at=:now "
                    "WHERE status='running' AND locked_by=:worker"
                ),
                {"now": now, "worker": worker_id},
            )
            session.commit()
            return result.rowcount  # ty: ignore[unresolved-attribute] - CursorResult has rowcount; ty loses it through Result[Any]

    def list_jobs(
        self,
        user_id: str,
        *,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Job]:
        with Session(self._engine) as session:
            stmt = select(Job).where(Job.user_id == user_id)
            if status is not None:
                stmt = stmt.where(Job.status == status)
            if kind is not None:
                stmt = stmt.where(Job.kind == kind)
            stmt = stmt.order_by(col(Job.created_at).desc()).limit(limit).offset(offset)
            return list(session.exec(stmt).all())

    def retry(self, job_id: str, user_id: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with Session(self._engine) as session:
            job = session.get(Job, job_id)
            if job is None or job.user_id != user_id or job.status != "failed":
                return False
            job.status = "pending"
            job.attempts = 0
            job.next_run_at = now
            job.last_error = None
            job.locked_by = None
            job.locked_at = None
            job.updated_at = now
            session.add(job)
            session.commit()
            return True

    def dismiss(self, job_id: str, user_id: str) -> bool:
        with Session(self._engine) as session:
            job = session.get(Job, job_id)
            if job is None or job.user_id != user_id or job.status not in ("done", "failed"):
                return False
            session.delete(job)
            session.commit()
            return True
