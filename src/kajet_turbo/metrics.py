"""Application-owned Prometheus registry, `/metrics` exposition and the shared-state sampler.

Importing this module is deliberately inert: no registry, listener or thread exists until
:func:`build_metrics` runs, so several apps in one process keep independent registries.

Storage mode is selected by ``PROMETHEUS_MULTIPROC_DIR`` alone (set for roles ``api`` and
``mcp`` in ``kajet_turbo/__init__.py``, see docs/specs/metrics-multiprocess.md). With it,
every uvicorn child writes to shared memory-mapped files and a scrape merges them; without
it (worker, all, tests) the app's own registry is exposed directly.
"""

from __future__ import annotations

import os
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    generate_latest,
    multiprocess,
    start_http_server,
)
from prometheus_client.core import GaugeMetricFamily
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response

from kajet_turbo.log import logger

METRICS_PATH = "/metrics"

# Shared-state snapshots have exactly one owner per deployment. `livemostrecent` (not
# `mostrecent`): the dead-child reaper only cleans `live*` gauges, so a mistakenly
# multiprocess owner fails clean — the series disappears instead of freezing.
_SNAPSHOT_MODE = "livemostrecent"


def _reap_dead_children(path: str) -> None:
    """Drop live-state gauge files left by children that no longer exist.

    Runs on the exposition path, where the directory scan is already paid for: a dead
    child otherwise keeps contributing to `livesum` gauges until something sweeps.
    Counters and histograms are untouched and survive a child restart.
    """
    pids: set[int] = set()
    for db_file in Path(path).glob("*_*.db"):
        tail = db_file.stem.rpartition("_")[2]
        if tail.isdigit():
            pids.add(int(tail))
    for pid in pids:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            multiprocess.mark_process_dead(pid, path)
        except PermissionError:
            continue  # alive, owned by another user


class SnapshotSource[T](ABC):
    """One shared-state collector (WAL size, queue depth, ...) with its own schedule.

    ``read`` is a pure read that may raise; ``publish`` only sets gauges and cannot fail.
    Splitting them makes a snapshot atomic: when ``read`` fails, no gauge is touched and
    the previous snapshot stays visible, with its age growing, rather than becoming zero.
    """

    def __init__(self, name: str, interval: float) -> None:
        self.name = name
        self.interval = interval

    @abstractmethod
    def read(self) -> T | None:
        """Take a snapshot; ``None`` means the data is unavailable and nothing is published."""

    @abstractmethod
    def publish(self, snapshot: T) -> None: ...


class Sampler:
    """Runs snapshot sources on their own schedule from one dedicated thread.

    A single thread is the read budget: at most one repository read is in flight, and it
    is independent of the job executor and of the `run_sync` limiter. Sources must open
    and close their session inside ``read`` so no reader snapshot outlives a sample.
    """

    def __init__(self, sources: Sequence[SnapshotSource], registry: CollectorRegistry) -> None:
        self._sources = tuple(sources)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_success: dict[str, float] = {}
        labels = ["collector"]
        self._success = Gauge(
            "kajet_sampler_success",
            "1 if the collector's latest sample succeeded, 0 if it failed",
            labels,
            registry=registry,
            multiprocess_mode=_SNAPSHOT_MODE,
        )
        self._last_success_ts = Gauge(
            "kajet_sampler_last_success_timestamp_seconds",
            "Unix time of the collector's latest successful sample",
            labels,
            registry=registry,
            multiprocess_mode=_SNAPSHOT_MODE,
        )
        self._duration = Gauge(
            "kajet_sampler_duration_seconds",
            "Duration of the collector's latest sample, successful or not",
            labels,
            registry=registry,
            multiprocess_mode=_SNAPSHOT_MODE,
        )
        self._errors = Counter(
            "kajet_sampler_errors",
            "Failed collector samples",
            labels,
            registry=registry,
        )
        registry.register(_AgeCollector(self))

    def last_success_ages(self, now: float | None = None) -> dict[str, float]:
        """Seconds since each collector last succeeded; absent until its first success."""
        now = time.time() if now is None else now
        with self._lock:
            return {name: now - ts for name, ts in self._last_success.items()}

    def sample_once(self, source: SnapshotSource) -> bool:
        """Sample one source and record its health. Never raises."""
        started = time.monotonic()
        try:
            snapshot = source.read()
            if snapshot is not None:
                source.publish(snapshot)
        except Exception:
            self._duration.labels(source.name).set(time.monotonic() - started)
            self._success.labels(source.name).set(0)
            self._errors.labels(source.name).inc()
            logger.warning("metrics_sample_failed", collector=source.name, exc_info=True)
            return False
        now = time.time()
        with self._lock:
            self._last_success[source.name] = now
        self._duration.labels(source.name).set(time.monotonic() - started)
        self._success.labels(source.name).set(1)
        self._last_success_ts.labels(source.name).set(now)
        return True

    def _run(self) -> None:
        due = {source.name: time.monotonic() for source in self._sources}
        while not self._stop.is_set():
            for source in self._sources:
                if due[source.name] <= time.monotonic():
                    self.sample_once(source)
                    due[source.name] = time.monotonic() + source.interval
            if self._stop.wait(max(0.0, min(due.values()) - time.monotonic())):
                break

    @contextmanager
    def running(self) -> Iterator[None]:
        """Run the sampling thread for the duration of the block; joined on exit.

        With no sources there is nothing to schedule, so no thread is started.
        """
        if not self._sources:
            yield
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="kajet-metrics-sampler", daemon=True)
        self._thread.start()
        try:
            yield
        finally:
            self._stop.set()
            self._thread.join()
            self._thread = None


class _AgeCollector:
    """Computes freshness at scrape time.

    A gauge written by the sampler thread would freeze together with a wedged thread,
    which is exactly the failure this must expose; computing it per scrape cannot.
    """

    def __init__(self, sampler: Sampler) -> None:
        self._sampler = sampler

    def collect(self) -> Iterable[GaugeMetricFamily]:
        family = GaugeMetricFamily(
            "kajet_sampler_age_seconds",
            "Seconds since the collector's latest successful sample",
            labels=["collector"],
        )
        for name, age in self._sampler.last_success_ages().items():
            family.add_metric([name], age)
        yield family


@dataclass(slots=True)
class Metrics:
    """What one app owns for metrics: its registry and, if it owns shared state, a sampler."""

    registry: CollectorRegistry = field(default_factory=CollectorRegistry)
    multiprocess_dir: str | None = None
    sampler: Sampler | None = None

    def exposition(self) -> bytes:
        if self.multiprocess_dir is None:
            return generate_latest(self.registry)
        _reap_dead_children(self.multiprocess_dir)
        # A fresh registry per scrape: MultiProcessCollector re-reads the directory on
        # every collect, and reusing one would register its collector twice.
        merged = CollectorRegistry()
        multiprocess.MultiProcessCollector(merged, path=self.multiprocess_dir)
        return generate_latest(merged)


def build_metrics(*, sample_shared: bool, sources: Sequence[SnapshotSource] = ()) -> Metrics:
    metrics = Metrics(multiprocess_dir=os.environ.get("PROMETHEUS_MULTIPROC_DIR") or None)
    if sample_shared:
        metrics.sampler = Sampler(sources, metrics.registry)
    return metrics


def prepare_multiprocess_dir() -> None:
    """Create and clear the multiprocess directory. Supervisor only, once, before children.

    Children never clear it (that would wipe their siblings' counters), and starting over
    a previous run's files would resume its totals instead of restarting at zero.
    """
    path = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if not path:
        return
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    for stale in directory.glob("*.db"):
        stale.unlink()


def add_metrics_route(app: Starlette, metrics: Metrics) -> None:
    """Serve ``GET /metrics`` at the app root.

    Never mount this under `/mcp` or `/api`: Caddy proxies those prefixes, so a path there
    would be public. Left at the root it matches no ingress allowlist and 404s outside.
    The exposition reads files, but goes through the ordinary threadpool rather than
    `run_sync` — that limiter is the DB budget, and a scrape must not queue behind (or
    ahead of) requests for it.
    """

    async def metrics_endpoint(request: Request) -> Response:
        try:
            body = await run_in_threadpool(metrics.exposition)
        except Exception:
            logger.warning("metrics_exposition_failed", exc_info=True)
            return Response(
                "metrics unavailable\n",
                status_code=500,
                media_type="text/plain",
                headers={"Cache-Control": "no-store"},
            )
        return Response(body, media_type=CONTENT_TYPE_LATEST, headers={"Cache-Control": "no-store"})

    app.add_route(METRICS_PATH, metrics_endpoint, methods=["GET"], include_in_schema=False)


@contextmanager
def serve_metrics(metrics: Metrics, *, port: int, addr: str = "0.0.0.0") -> Iterator[int]:
    """Own the worker role's HTTP listener: bound on entry, port released on exit.

    The worker has no ASGI app, so it exposes its own registry over a tiny WSGI server.
    Yields the bound port (useful when ``port`` is 0).
    """
    httpd, thread = start_http_server(port, addr, registry=metrics.registry)
    try:
        yield httpd.server_port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
