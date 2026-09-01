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

    def value(self, field: str) -> float | int:
        # Free-threaded 3.14t: dict.get without a lock is not guaranteed atomic against a
        # concurrent add() on the same field, so read under the same lock.
        with self._lock:
            return self.fields.get(field, 0)


def current() -> PerfSpan | None:
    return _span.get()


def record(field: str, ms: float) -> None:
    """Add ``ms`` milliseconds to ``field`` on the active span (no-op if none)."""
    span = _span.get()
    if span is not None:
        span.add(field, round(ms, 1))


def peek(field: str) -> float:
    """Current accumulated value of ``field`` on the active span (0.0 if none).

    Read-side twin of ``record``: lets a caller snapshot a running sum before/after a
    sub-operation to attribute just that delta (see run_sync's per-dispatch db_ms)."""
    span = _span.get()
    return float(span.value(field)) if span is not None else 0.0


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


class _ExclusionLedger:
    """Thread-safe accumulator of ms excluded from a field within one local scope."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._totals: dict[str, float] = {}

    def add(self, field: str, ms: float) -> None:
        with self._lock:
            self._totals[field] = self._totals.get(field, 0.0) + ms

    def pop(self, field: str) -> float:
        with self._lock:
            return self._totals.pop(field, 0.0)


_local_exclusions: ContextVar[_ExclusionLedger | None] = ContextVar(
    "perf_local_exclusions", default=None
)


@contextmanager
def local_exclusion_scope():
    """Open a scope that ``excluded_from()`` calls report into, independent of any span.

    Yields ``pop(field) -> float``: ms excluded from ``field`` by nested
    ``excluded_from(field)`` calls made during this scope. For a caller doing its own
    wall-clock timing (``DbRepository.timed_session``) that needs to subtract the same
    nested work a span already excludes from its aggregate — so the *local* figure still
    means "DB time" even when no span is active to receive the span-side subtraction.
    """
    ledger = _ExclusionLedger()
    token = _local_exclusions.set(ledger)
    try:
        yield ledger.pop
    finally:
        _local_exclusions.reset(token)


@contextmanager
def excluded_from(field: str):
    """Run the wrapped block without its wall time counting toward ``field``.

    Only makes sense nested inside an enclosing ``timed(field)``/wall-clock window that
    would otherwise attribute this block's own duration to ``field`` too (e.g. a git
    commit running inside ``DbRepository.operation()``'s ``db_ms`` window) — used
    standalone it just drives ``field`` negative. Subtracts this block's elapsed time
    from ``field`` on the active span (if any) and from the active
    ``local_exclusion_scope`` (if any).

    Both ``timed()`` and this function independently round their own elapsed time
    before adding, so the two roundings don't cancel exactly — expect ~0.1ms drift on
    the net value.
    """
    span = _span.get()
    ledger = _local_exclusions.get()
    if span is None and ledger is None:
        yield
        return
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = round((time.monotonic() - start) * 1000, 1)
        if span is not None:
            span.add(field, -elapsed)
        if ledger is not None:
            ledger.add(field, elapsed)
