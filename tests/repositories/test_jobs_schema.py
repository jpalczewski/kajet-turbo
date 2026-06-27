from sqlalchemy import inspect, text
from sqlmodel import Session

from kajet_turbo.db import Database


def test_jobs_table_columns(database: Database):
    cols = {c["name"] for c in inspect(database.engine).get_columns("jobs")}
    assert cols == {
        "id",
        "kind",
        "user_id",
        "dedup_key",
        "payload",
        "status",
        "attempts",
        "max_attempts",
        "next_run_at",
        "locked_by",
        "locked_at",
        "last_error",
        "created_at",
        "updated_at",
    }


def test_jobs_pending_dedup_is_partial_unique(database: Database):
    with Session(database.engine) as session:
        sql = session.execute(  # ty: ignore[deprecated] - raw SQL
            text(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_jobs_pending_dedup'"
            )
        ).scalar_one()
    assert sql is not None
    upper = sql.upper()
    assert "UNIQUE" in upper
    assert "WHERE" in upper and "STATUS" in upper and "pending" in sql


def test_jobs_claim_index_exists(database: Database):
    names = {ix["name"] for ix in inspect(database.engine).get_indexes("jobs")}
    assert "ix_jobs_claim" in names
    assert "ix_jobs_user_id" in names
