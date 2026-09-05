"""Standalone background worker (KAJET_ROLE=worker).

A synchronous poll loop claims runnable jobs and runs their handlers on a thread
pool — no asyncio. On free-threaded Python the pool threads run truly in parallel,
which suits the heterogeneous I/O-bound jobs (git push, embedding HTTP). The DB is
the queue; this process only reads it to claim and writes lifecycle transitions
via JobRepository. There are two wait points: an idle backoff
(``stop_event.wait(poll_interval)``), taken when a claim attempt finds nothing
runnable, and a saturated wait (``futures.wait(..., return_when=FIRST_COMPLETED)``),
taken when the pool is full and more work may still be queued. The idle backoff is
where a cross-process nudge would later replace polling; the saturated wait is
already event-driven — it wakes as soon as a slot frees rather than on a fixed
tick, so sustained throughput is bounded by claim/handler cost, not poll_interval."""

import json
import os
import signal
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import TypeVar

from sqlalchemy import Engine

from kajet_turbo.log import logger
from kajet_turbo.models import Job
from kajet_turbo.perf import perf_span
from kajet_turbo.repositories.jobs import JobRepository

Handler = Callable[[dict], None]
_T = TypeVar("_T")

_HANDLERS: dict[str, Handler] = {}


def register_handler(kind: str, handler: Handler) -> None:
    _HANDLERS[kind] = handler


def get_handler(kind: str) -> Handler | None:
    return _HANDLERS.get(kind)


def run_job(repo: JobRepository, job: Job, registry: dict[str, Handler]) -> None:
    """Execute one claimed job. Unknown kind -> terminal fail (a misrouted job must
    not retry forever). Handler exception -> retrying fail. Success -> complete.

    The repository write itself is wrapped separately from the handler: a write failure
    (e.g. a transient SQLite lock) must still produce a ``job_finished`` line before it
    propagates, since ``run_worker`` never calls ``Future.result()`` and would otherwise
    lose it silently.
    """
    started = time.monotonic()
    queue_wait_ms = round(max(0.0, time.time() - job.next_run_at) * 1000)
    error: Exception | None = None
    repo_error: Exception | None = None

    def write(fn: Callable[[], _T]) -> _T | None:
        nonlocal repo_error
        try:
            return fn()
        except Exception as exc:
            repo_error = exc
            return None

    with perf_span() as span:
        handler = registry.get(job.kind)
        if handler is None:
            outcome = "no_handler"
            level = "WARNING"
            write(lambda: repo.fail_terminal(job.id, f"no handler for kind {job.kind!r}"))
        else:
            try:
                handler(json.loads(job.payload))
            except Exception as exc:
                error = exc
                error_message = str(exc)
                level = "WARNING"
                status = write(lambda: repo.fail(job.id, error_message))
                # A write failure leaves the job's real status unknown (the transaction
                # rolled back), so it is not reported as either "failed" or "retrying".
                if repo_error is not None:
                    outcome = "unknown"
                else:
                    outcome = "failed" if status == "failed" else "retrying"
            else:
                outcome = "completed"
                level = "INFO"
                write(lambda: repo.complete(job.id))

        perf_fields = span.fields if span else {}
        log_error = repo_error or error
        sink = logger.opt(exception=log_error) if log_error is not None else logger
        sink.log(
            level if repo_error is None else "ERROR",
            "job_finished",
            job_id=job.id,
            kind=job.kind,
            outcome=outcome,
            repo_write_failed=repo_error is not None,
            duration_ms=round((time.monotonic() - started) * 1000),
            queue_wait_ms=queue_wait_ms,
            **perf_fields,
        )
    if repo_error is not None:
        raise repo_error


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def run_worker(
    engine: Engine,
    *,
    worker_id: str | None = None,
    registry: dict[str, Handler] | None = None,
    poll_interval: float = 1.0,
    concurrency: int = 4,
    stale_after: float = 300.0,
    stop_event: threading.Event | None = None,
) -> None:
    """Run the claim/dispatch loop until ``stop_event`` is set. When ``stop_event``
    is None, install SIGTERM/SIGINT handlers (entrypoint use, main thread only);
    when provided, the caller controls shutdown (tests)."""
    worker_id = worker_id or _default_worker_id()
    registry = _HANDLERS if registry is None else registry
    repo = JobRepository(engine)

    if stop_event is None:
        stop_event = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop_event.set())

    logger.info("worker_start", worker_id=worker_id, concurrency=concurrency)
    inflight: set[Future] = set()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        while not stop_event.is_set():
            inflight = {f for f in inflight if not f.done()}
            nothing_runnable = False
            while len(inflight) < concurrency:
                job = repo.claim(worker_id, stale_after=stale_after)
                if job is None:
                    nothing_runnable = True
                    break
                inflight.add(pool.submit(run_job, repo, job, registry))
            if nothing_runnable and not inflight:
                stop_event.wait(poll_interval)
            else:
                # Either the pool is saturated, or it isn't but jobs are still
                # inflight (burst draining to fewer runnable jobs than concurrency).
                # Wake as soon as a slot frees instead of waiting a fixed tick, so
                # throughput scales with claim/handler cost rather than
                # poll_interval; a run that never completes within the window still
                # re-checks stop_event every poll_interval, same shutdown latency
                # as before.
                wait(inflight, timeout=poll_interval, return_when=FIRST_COMPLETED)
        # Leaving the `with` block waits for in-flight jobs to finish (graceful
        # drain). reset_running_to_pending then re-queues anything a hard kill could
        # have left running; after a clean drain it finds nothing.
    reset = repo.reset_running_to_pending(worker_id)
    logger.info("worker_stop", worker_id=worker_id, reset=reset)
