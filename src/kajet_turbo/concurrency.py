"""Single async/sync boundary: dispatch sync DB/git/file work to worker threads.

The dedicated limiter (10 = SQLAlchemy pool_size 5 + max_overflow 5) keeps DB
work from starving AnyIO's default 40-thread pool and from oversubscribing the
connection pool. On free-threaded Python these threads truly run in parallel.
"""
import asyncio
import threading
from collections.abc import Callable
from functools import partial
from typing import Any

import anyio.to_thread
from anyio import CapacityLimiter

_LIMIT = 10
_limiters: dict[Any, CapacityLimiter] = {}
_limiters_guard = threading.Lock()


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


async def run_sync(fn: Callable, /, *args, **kwargs):
    return await anyio.to_thread.run_sync(
        partial(fn, *args, **kwargs), limiter=_db_limiter()
    )
