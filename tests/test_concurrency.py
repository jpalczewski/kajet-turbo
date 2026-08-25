import threading

import pytest

from kajet_turbo.concurrency import run_sync
from tests.helpers import entries_named, read_log_entries


async def test_run_sync_runs_in_worker_thread_and_returns_value():
    main_thread = threading.get_ident()

    def work(x: int, *, y: int) -> int:
        assert threading.get_ident() != main_thread
        return x + y

    assert await run_sync(work, 1, y=2) == 3


async def test_run_sync_propagates_exceptions():
    def boom() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await run_sync(boom)


async def test_run_sync_bounded_concurrency():
    import anyio

    active = 0
    peak = 0
    lock = threading.Lock()

    def work() -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        import time

        time.sleep(0.05)
        with lock:
            active -= 1

    async with anyio.create_task_group() as tg:
        for _ in range(30):
            tg.start_soon(run_sync, work)
    assert peak <= 10


async def test_run_sync_propagates_contextvars():
    import contextvars

    var: contextvars.ContextVar[str] = contextvars.ContextVar("var")
    var.set("from-request")

    assert await run_sync(var.get) == "from-request"


async def test_slow_sync_reports_per_dispatch_db_ms(capsys, monkeypatch):
    import time

    from kajet_turbo import concurrency, perf
    from kajet_turbo.log import setup_logging

    setup_logging()
    monkeypatch.setattr(concurrency, "_SLOW_SYNC_MS", 1.0)  # log every dispatch

    def work(db_ms_to_add: float) -> None:
        # Real sleep time tracks the fake db_ms so residual_ms (elapsed - wait - db)
        # stays non-negative, as it would for a real DB call.
        time.sleep(db_ms_to_add / 1000.0)
        perf.record("db_ms", db_ms_to_add)

    with perf.perf_span():
        await concurrency.run_sync(work, 30.0)  # span db_ms: 0 -> 30
        await concurrency.run_sync(work, 70.0)  # span db_ms: 30 -> 100

    slow = entries_named(read_log_entries(capsys), "slow_sync")
    assert len(slow) == 2
    by_db = sorted(e["db_ms"] for e in slow)
    assert by_db == [30, 70]  # NOT [30, 100] — per-dispatch delta, not cumulative
    for e in slow:
        assert "limiter_wait_ms" in e and "residual_ms" in e and "borrowed" in e
        assert e["residual_ms"] >= 0  # sequential dispatch => non-negative
