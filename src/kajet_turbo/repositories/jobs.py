"""The job queue lives in SQLite. This repository is the only writer of `jobs`
rows: enqueue (with debounce), atomic claim, and lifecycle transitions. Time
values are epoch seconds; ``now`` is injectable so tests are deterministic."""

import json
import time
from dataclasses import dataclass

from nanoid import generate
from sqlalchemy import func, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, col, select

from kajet_turbo.models import Job
from kajet_turbo.repositories import DbRepository

# Lower claims first (nice-like convention). Every enqueue site defaults to
# PRIORITY_DEFAULT unless it opts into PRIORITY_BULK — see #151.
PRIORITY_DEFAULT = 0
PRIORITY_BULK = 10


@dataclass(frozen=True, slots=True)
class JobEntry:
    """One job to enqueue as part of a batch — see ``enqueue_many``/``enqueue_many_in_session``."""

    payload: dict
    dedup_key: str | None = None
    user_id: str | None = None


def backoff_seconds(attempts: int, base: float = 2.0, cap: float = 300.0) -> float:
    """Exponential backoff for the Nth retry (attempts >= 1), capped at ``cap``."""
    return min(cap, base * (2 ** (attempts - 1)))


# One atomic statement: pick the single most-overdue runnable row, within the highest
# priority lane, and lock it. Eligible = a pending job whose time has come, OR a running
# job whose worker died (locked_at older than the stale cutoff). SQLite serializes the
# write, so two workers never claim the same row. Additionally, a job is skipped while
# another RUNNING job shares its (non-NULL) dedup_key — this serializes same-key work
# (e.g. one push per workspace at a time) and coalesces a burst into one follow-up.
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
        ORDER BY j.priority, j.next_run_at
        LIMIT 1
    )
    RETURNING *
    """
)


class JobRepository(DbRepository):
    repository_name = "jobs"

    def enqueue_in_session(
        self,
        session: Session,
        kind: str,
        payload: dict,
        *,
        dedup_key: str | None = None,
        user_id: str | None = None,
        max_attempts: int = 5,
        delay: float = 0.0,
        priority: int = PRIORITY_DEFAULT,
        now: float | None = None,
    ) -> str:
        """Enqueue without committing, for callers that atomically persist related state."""
        now = time.time() if now is None else now
        run_at = now + delay
        body = json.dumps(payload)
        job_id = generate()
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
                    priority=priority,
                    created_at=now,
                    updated_at=now,
                )
            )
            return job_id

        # Debounce: one pending job per (kind, dedup_key). On conflict with the
        # partial unique index, re-arm the existing pending row instead of
        # inserting a duplicate. The priority never regresses on conflict — a more
        # urgent duplicate enqueue (e.g. a manual reindex re-arming a pending bulk
        # row) wins, but a bulk re-enqueue never demotes an already-urgent row.
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
                priority=priority,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[Job.kind, Job.dedup_key],  # ty: ignore[invalid-argument-type] — SQLAlchemy column descriptors satisfy DDLConstraintColumnRole at runtime; ty infers str|None from the model field annotation
                index_where=(Job.status == "pending"),  # ty: ignore[invalid-argument-type] — ColumnElement.__eq__ returns ColumnElement[bool], not bool; ty loses the overload
                set_={
                    "next_run_at": run_at,
                    "priority": func.min(Job.priority, priority),
                    "updated_at": now,
                },
            )
        )
        session.exec(stmt)
        return session.exec(
            select(Job.id).where(
                Job.kind == kind,
                Job.dedup_key == dedup_key,
                Job.status == "pending",
            )
        ).one()

    def enqueue(
        self,
        kind: str,
        payload: dict,
        *,
        dedup_key: str | None = None,
        user_id: str | None = None,
        max_attempts: int = 5,
        delay: float = 0.0,
        priority: int = PRIORITY_DEFAULT,
        now: float | None = None,
    ) -> str:
        with self.operation(
            "enqueue", kind=kind, user_id=user_id, deduplicated=dedup_key is not None
        ) as operation:
            session = operation.session
            job_id = self.enqueue_in_session(
                session,
                kind,
                payload,
                dedup_key=dedup_key,
                user_id=user_id,
                max_attempts=max_attempts,
                delay=delay,
                priority=priority,
                now=now,
            )
            session.commit()
            operation.add_fields(job_id=job_id)
            return job_id

    def enqueue_many_in_session(
        self,
        session: Session,
        kind: str,
        entries: list[JobEntry],
        *,
        max_attempts: int = 5,
        delay: float = 0.0,
        priority: int = PRIORITY_DEFAULT,
        now: float | None = None,
    ) -> list[str]:
        """Enqueue N same-kind jobs without committing, one INSERT/upsert per entry, so a
        fan-out lands in the caller's transaction instead of opening N write sessions
        against SQLite while the caller may already hold the write lock."""
        now = time.time() if now is None else now
        return [
            self.enqueue_in_session(
                session,
                kind,
                entry.payload,
                dedup_key=entry.dedup_key,
                user_id=entry.user_id,
                max_attempts=max_attempts,
                delay=delay,
                priority=priority,
                now=now,
            )
            for entry in entries
        ]

    def enqueue_many(
        self,
        kind: str,
        entries: list[JobEntry],
        *,
        max_attempts: int = 5,
        delay: float = 0.0,
        priority: int = PRIORITY_DEFAULT,
        now: float | None = None,
    ) -> list[str]:
        with self.operation("enqueue_many", kind=kind, count=len(entries)) as operation:
            session = operation.session
            job_ids = self.enqueue_many_in_session(
                session,
                kind,
                entries,
                max_attempts=max_attempts,
                delay=delay,
                priority=priority,
                now=now,
            )
            session.commit()
            operation.report_count(len(job_ids))
            return job_ids

    def claim(
        self, worker_id: str, *, now: float | None = None, stale_after: float = 300.0
    ) -> Job | None:
        now = time.time() if now is None else now
        with self.operation("claim", worker_id=worker_id) as operation:
            session = operation.session
            row = session.execute(  # ty: ignore[deprecated] - raw SQL
                _CLAIM_SQL,
                {"worker": worker_id, "now": now, "stale_cutoff": now - stale_after},
            ).fetchone()
            session.commit()
            if row is None:
                operation.suppress_log()
                return None
            job = Job(**row._mapping)
            operation.outcome = "claimed"
            queue_wait_ms = round(max(0.0, now - job.next_run_at) * 1000)
            operation.add_fields(
                job_id=job.id, kind=job.kind, priority=job.priority, queue_wait_ms=queue_wait_ms
            )
            return job

    def complete(self, job_id: str, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.operation("complete", job_id=job_id) as operation:
            session = operation.session
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
    ) -> str | None:
        """Record a handler failure. Returns the job's resulting status
        (``"failed"`` or ``"pending"``), or ``None`` if the job no longer exists."""
        now = time.time() if now is None else now
        with self.operation("fail", job_id=job_id) as operation:
            session = operation.session
            job = session.get(Job, job_id)
            if job is None:
                operation.suppress_log()
                return None
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
            operation.outcome = job.status
            operation.add_fields(kind=job.kind, attempts=job.attempts)
            return job.status

    def fail_terminal(self, job_id: str, error: str, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.operation("fail_terminal", job_id=job_id) as operation:
            session = operation.session
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "UPDATE jobs SET status='failed', last_error=:err, updated_at=:now WHERE id=:id"
                ),
                {"err": error, "now": now, "id": job_id},
            )
            session.commit()

    def reset_running_to_pending(self, worker_id: str, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self.operation("reset_running_to_pending", worker_id=worker_id) as operation:
            session = operation.session
            result = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "UPDATE jobs SET status='pending', next_run_at=:now, locked_by=NULL, "
                    "locked_at=NULL, updated_at=:now "
                    "WHERE status='running' AND locked_by=:worker"
                ),
                {"now": now, "worker": worker_id},
            )
            session.commit()
            count = result.rowcount  # ty: ignore[unresolved-attribute] - CursorResult has rowcount; ty loses it through Result[Any]
            operation.report_count(count)
            return count

    def list_jobs(
        self,
        user_id: str,
        *,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Job]:
        with self.timed_session() as session:
            stmt = select(Job).where(Job.user_id == user_id)
            if status is not None:
                stmt = stmt.where(Job.status == status)
            if kind is not None:
                stmt = stmt.where(Job.kind == kind)
            stmt = stmt.order_by(col(Job.created_at).desc()).limit(limit).offset(offset)
            return list(session.exec(stmt).all())

    def retry(self, job_id: str, user_id: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self.operation("retry", job_id=job_id, user_id=user_id) as operation:
            session = operation.session
            job = session.get(Job, job_id)
            if job is None or job.user_id != user_id or job.status != "failed":
                operation.suppress_log()
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

    def sweep_done(self, older_than: float = 86400.0, *, now: float | None = None) -> int:
        """Purge ``done`` rows older than ``older_than`` seconds. ``failed`` rows are
        kept — they stay user-visible in the jobs API until retried or dismissed."""
        now = time.time() if now is None else now
        with self.operation("sweep_done") as operation:
            session = operation.session
            result = session.execute(  # ty: ignore[deprecated] - raw SQL
                text("DELETE FROM jobs WHERE status='done' AND updated_at < :cutoff"),
                {"cutoff": now - older_than},
            )
            session.commit()
            count = result.rowcount  # ty: ignore[unresolved-attribute] - CursorResult has rowcount; ty loses it through Result[Any]
            # No log here on purpose: the only caller already reports the count as
            # `outbox_sweep(jobs_purged=...)` in server.py, gated on it being abnormal.
            operation.suppress_log()
            return count

    def dismiss(self, job_id: str, user_id: str) -> bool:
        with self.operation("dismiss", job_id=job_id, user_id=user_id) as operation:
            session = operation.session
            job = session.get(Job, job_id)
            if job is None or job.user_id != user_id or job.status not in ("done", "failed"):
                operation.suppress_log()
                return False
            session.delete(job)
            session.commit()
            return True

    def delete_for_workspace(self, user_id: str, workspace: str) -> None:
        """Drop every job (any status) scoped to a deleted workspace. Jobs carry no
        workspace column — payload.workspace is the invariant every workspace-scoped
        kind (push_workspace/reconcile_links/embed_note) shares, and what JobService
        already relies on to render the dashboard. Global jobs (sweep_outbox) have a
        NULL user_id and no payload.workspace, so they're structurally excluded."""
        with self.operation(
            "delete_for_workspace", user_id=user_id, workspace=workspace
        ) as operation:
            session = operation.session
            result = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "DELETE FROM jobs WHERE user_id=:user_id"
                    " AND json_extract(payload, '$.workspace')=:workspace"
                ),
                {"user_id": user_id, "workspace": workspace},
            )
            count = result.rowcount  # ty: ignore[unresolved-attribute] - CursorResult has rowcount; ty loses it through Result[Any]
            session.commit()
            operation.report_count(count)
