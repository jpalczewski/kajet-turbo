"""Standalone background worker (KAJET_ROLE=worker).

A synchronous poll loop claims runnable jobs and runs their handlers on a thread
pool — no asyncio. On free-threaded Python the pool threads run truly in parallel,
which suits the heterogeneous I/O-bound jobs (git push, embedding HTTP). The DB is
the queue; this process only reads it to claim and writes lifecycle transitions
via JobRepository. The single wait point (``stop_event.wait(poll_interval)``) is
where a cross-process nudge would later replace polling, with no change to claim
semantics."""

import json
import os
import signal
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
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
            while len(inflight) < concurrency:
                job = repo.claim(worker_id, stale_after=stale_after)
                if job is None:
                    break
                inflight.add(pool.submit(run_job, repo, job, registry))
            stop_event.wait(poll_interval)
        # Leaving the `with` block waits for in-flight jobs to finish (graceful
        # drain). reset_running_to_pending then re-queues anything a hard kill could
        # have left running; after a clean drain it finds nothing.
    reset = repo.reset_running_to_pending(worker_id)
    logger.info("worker_stop", worker_id=worker_id, reset=reset)
