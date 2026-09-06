"""Regression coverage for the cross-process migration lock in kajet_turbo/db.py."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from kajet_turbo.db import Database


def test_concurrent_workers_migrating_fresh_db_do_not_race(tmp_path: Path):
    """N workers constructing Database() against one brand-new DB file must all
    succeed.

    Reproduces the api/mcp/worker startup race: multiple workers of one role
    boot against a shared, not-yet-migrated DB file and each worker's own
    Database() calls `alembic upgrade head`. Without the cross-process lock in
    `_run_migrations`, the losing workers crash with
    `sqlite3.OperationalError: table alembic_version already exists` (observed
    with `KAJET_ROLE=api API_WORKERS=2` against a fresh volume).

    `fcntl.flock` locks are scoped to the open file description, not the
    process, so threads each doing their own `os.open()` reproduce the same
    race as separate OS processes would, without paying for N interpreter
    spawns.
    """

    def construct() -> None:
        Database(str(tmp_path / "shared.db")).close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        # .result() on every future re-raises the first worker's exception, if any.
        for future in [pool.submit(construct) for _ in range(8)]:
            future.result()
