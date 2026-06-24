"""Per-operation performance accounting.

A ``PerfSpan`` accumulates phase timings/counters for one operation (a tool call or
HTTP route). Deep layers — git, embedding, cache, the run_sync dispatcher — feed the
*active* span via ``record``/``incr``/``timed`` without threading parameters through;
they are no-ops when no span is active. The entry decorator opens a span and merges its
collected fields into the operation's completion log line, so perf is one structured
line per op, correlated by the session_id/user_id already bound on that line.

anyio copies the context into the run_sync worker thread, so a span opened at the async
entry point is visible to the synchronous service/git/embedding code it dispatches.
"""

import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

_ENABLED = os.getenv("PERF_LOG", "1").lower() not in ("0", "false", "no", "")

_span: ContextVar[PerfSpan | None] = ContextVar("perf_span", default=None)


class PerfSpan:
    """Thread-safe accumulator of named float (ms) sums and int counters."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.fields: dict[str, float | int] = {}

    def add(self, field: str, value: float | int) -> None:
        with self._lock:
            self.fields[field] = self.fields.get(field, 0) + value


def current() -> PerfSpan | None:
    return _span.get()


def record(field: str, ms: float) -> None:
    """Add ``ms`` milliseconds to ``field`` on the active span (no-op if none)."""
    span = _span.get()
    if span is not None:
        span.add(field, round(ms, 1))


def incr(field: str, n: int = 1) -> None:
    """Increment a counter on the active span (no-op if none)."""
    span = _span.get()
    if span is not None:
        span.add(field, n)


@contextmanager
def timed(field: str):
    """Measure the wrapped block into ``field`` (ms) on the active span (no-op if none)."""
    span = _span.get()
    if span is None:
        yield
        return
    start = time.monotonic()
    try:
        yield
    finally:
        span.add(field, round((time.monotonic() - start) * 1000, 1))


@contextmanager
def perf_span():
    """Open a fresh span for the duration; yields it (or ``None`` when PERF_LOG is off).

    Read ``span.fields`` after the wrapped call to merge into the completion log line.
    """
    if not _ENABLED:
        yield None
        return
    span = PerfSpan()
    token = _span.set(span)
    try:
        yield span
    finally:
        _span.reset(token)
