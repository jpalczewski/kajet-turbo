"""The job queue lives in SQLite. This repository is the only writer of `jobs`
rows: enqueue (with debounce), atomic claim, and lifecycle transitions. Time
values are epoch seconds; ``now`` is injectable so tests are deterministic."""

import json
import time

from nanoid import generate
from sqlalchemy import Engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, select

from kajet_turbo.models import Job


def backoff_seconds(attempts: int, base: float = 2.0, cap: float = 300.0) -> float:
    """Exponential backoff for the Nth retry (attempts >= 1), capped at ``cap``."""
    return min(cap, base * (2 ** (attempts - 1)))


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
