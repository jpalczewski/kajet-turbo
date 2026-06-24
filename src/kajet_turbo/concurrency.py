"""Single async/sync boundary: dispatch sync DB/git/file work to worker threads.

The dedicated limiter (10 = SQLAlchemy pool_size 5 + max_overflow 5) keeps DB
work from starving AnyIO's default 40-thread pool and from oversubscribing the
connection pool. On free-threaded Python these threads truly run in parallel.

asyncio-only (uses get_running_loop). Cancellation: the sync fn always runs to
completion (abandon_on_cancel=False), but a *native* task.cancel() — e.g. a
client disconnect — releases the caller (and the limiter token) before the
thread finishes; pool checkout timeout + busy_timeout cover that window.
"""

import asyncio
import os
import threading
import time
from collections.abc import Callable
from functools import partial
from typing import Any

import anyio.to_thread
from anyio import CapacityLimiter
from loguru import logger

_LIMIT = 10
_limiters: dict[Any, CapacityLimiter] = {}
_limiters_guard = threading.Lock()

# Every blocking op (DB, git, embedding, file I/O) funnels through run_sync, so
# this is the one place that sees them all. Logging slow dispatches with the op
# name + pool saturation localizes performance problems without per-call_site
# instrumentation. Tune via SLOW_SYNC_MS; set 0 to disable.
_SLOW_SYNC_MS = float(os.getenv("SLOW_SYNC_MS", "500"))


def _db_limiter() -> CapacityLimiter:
    # CapacityLimiter binds to the running event loop; tests create many loops,
    # so keep one limiter per loop.
    loop = asyncio.get_running_loop()
    with _limiters_guard:
        limiter = _limiters.get(loop)
        if limiter is None:
            limiter = CapacityLimiter(_LIMIT)
            _limiters[loop] = limiter
        return limiter


async def run_sync[**P, T](fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    if not _SLOW_SYNC_MS:
        return await anyio.to_thread.run_sync(partial(fn, *args, **kwargs), limiter=_db_limiter())
    limiter = _db_limiter()
    start = time.monotonic()
    try:
        return await anyio.to_thread.run_sync(partial(fn, *args, **kwargs), limiter=limiter)
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000
        if elapsed_ms >= _SLOW_SYNC_MS:
            # borrowed == _LIMIT alongside a long duration points at pool/limiter
            # saturation (work queued); low borrowed points at a genuinely slow op.
            logger.warning(
                "slow_sync",
                op=getattr(fn, "__qualname__", repr(fn)),
                duration_ms=round(elapsed_ms),
                borrowed=limiter.borrowed_tokens,
                limit=_LIMIT,
            )
