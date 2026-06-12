import threading

import pytest

from kajet_turbo.concurrency import run_sync


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
