"""Owned metrics registries, sampler health and the worker listener (#311)."""

import socket
import threading
from contextlib import closing
from pathlib import Path

import httpx2
import pytest
from prometheus_client import CollectorRegistry, Gauge, generate_latest
from starlette.testclient import TestClient

from kajet_turbo import perf
from kajet_turbo.dependencies import AppConfig
from kajet_turbo.metrics import (
    Metrics,
    SnapshotSource,
    build_metrics,
    prepare_multiprocess_dir,
    serve_metrics,
)
from kajet_turbo.server import (
    _sample_shared_default,
    build_api_app,
    build_app,
    build_mcp_app,
)


class FakeSource(SnapshotSource[int]):
    """Publishes whatever ``value`` holds; ``fail`` makes the read raise."""

    def __init__(self, name: str = "queue", interval: float = 15.0) -> None:
        super().__init__(name, interval)
        self.registry = CollectorRegistry()
        self.gauge = Gauge(
            "test_snapshot", "snapshot", registry=self.registry, multiprocess_mode="livemostrecent"
        )
        self.value: int | None = 7
        self.fail = False
        self.published: list[int] = []
        self.on_publish = threading.Event()

    def read(self) -> int | None:
        if self.fail:
            raise RuntimeError("database is locked")
        return self.value

    def publish(self, snapshot: int) -> None:
        self.published.append(snapshot)
        self.gauge.set(snapshot)
        self.on_publish.set()


def _sample(metrics: Metrics, name: str, **labels: str) -> float | None:
    """Read one series from the app's registry; None when it is absent."""
    assert metrics.registry is not None
    return metrics.registry.get_sample_value(name, labels)


def _sampler_metrics(source: SnapshotSource) -> Metrics:
    metrics = build_metrics(sample_shared=True, sources=[source])
    assert metrics.sampler is not None
    return metrics


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _assert_port_free(host: str, port: int) -> None:
    """Bind like a real server (SO_REUSEADDR): the scrape leaves TIME_WAIT behind, which
    is not a leaked listener, so a plain bind would fail for the wrong reason."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))


def _config(tmp_path: Path, **overrides) -> AppConfig:
    return AppConfig(
        db_path=str(tmp_path / "kajet.db"),
        workspaces_dir=str(tmp_path / "workspaces"),
        mcp_base_url="http://localhost:8000",
        secret_key="test-secret",
        **overrides,
    )


# --- registry ownership -------------------------------------------------------------


def test_each_app_owns_its_registry(tmp_path: Path):
    first = build_api_app(_config(tmp_path / "a", metrics_sample_shared=True))
    second = build_api_app(_config(tmp_path / "b", metrics_sample_shared=True))

    reg_a = first.state.resources.metrics.registry
    reg_b = second.state.resources.metrics.registry

    assert reg_a is not reg_b
    # Same metric names in both apps would raise on a shared registry; here they coexist.
    assert generate_latest(reg_a) is not None
    assert generate_latest(reg_b) is not None


def test_no_multiprocess_dir_means_single_process_exposition():
    metrics = build_metrics(sample_shared=False)

    assert metrics.multiprocess_dir is None
    assert metrics.sampler is None
    assert metrics.exposition() == generate_latest(metrics.registry)


def test_gauges_never_use_the_forbidden_all_mode():
    """`all` emits a series per PID and grows on every child restart (#309 §1.5)."""
    metrics = _sampler_metrics(FakeSource())
    gauge_names = {
        "kajet_sampler_success",
        "kajet_sampler_last_success_timestamp_seconds",
        "kajet_sampler_duration_seconds",
    }
    families = {f.name: f for f in metrics.registry.collect()}

    assert gauge_names <= set(families)
    for collector in metrics.registry._collector_to_names:
        if isinstance(collector, Gauge):
            assert collector._multiprocess_mode != "all"


# --- sampler health -----------------------------------------------------------------


def test_successful_sample_publishes_and_records_health():
    source = FakeSource()
    metrics = _sampler_metrics(source)
    sampler = metrics.sampler
    assert sampler is not None

    assert sampler.sample_once(source) is True

    assert source.registry.get_sample_value("test_snapshot") == 7
    assert _sample(metrics, "kajet_sampler_success", collector="queue") == 1
    assert _sample(metrics, "kajet_sampler_last_success_timestamp_seconds", collector="queue")
    assert _sample(metrics, "kajet_sampler_duration_seconds", collector="queue") is not None
    age = _sample(metrics, "kajet_sampler_age_seconds", collector="queue")
    assert age is not None
    assert 0 <= age < 5


def test_failed_sample_keeps_previous_snapshot_and_counts_the_error():
    source = FakeSource()
    metrics = _sampler_metrics(source)
    sampler = metrics.sampler
    assert sampler is not None
    sampler.sample_once(source)
    first_success = _sample(
        metrics, "kajet_sampler_last_success_timestamp_seconds", collector="queue"
    )

    source.fail = True
    source.value = 0  # would be published if a failed read were replaced by zeros
    assert sampler.sample_once(source) is False

    assert source.registry.get_sample_value("test_snapshot") == 7  # retained, not zeroed
    assert _sample(metrics, "kajet_sampler_success", collector="queue") == 0
    assert _sample(metrics, "kajet_sampler_errors_total", collector="queue") == 1
    assert (
        _sample(metrics, "kajet_sampler_last_success_timestamp_seconds", collector="queue")
        == first_success
    )
    # Freshness is derived from the last success, so it keeps growing through failures.
    assert first_success is not None
    ages = sampler.last_success_ages(now=first_success + 90)
    assert ages["queue"] == pytest.approx(90)


def test_unavailable_snapshot_publishes_nothing_but_is_healthy():
    source = FakeSource()
    source.value = None
    metrics = _sampler_metrics(source)
    sampler = metrics.sampler
    assert sampler is not None

    assert sampler.sample_once(source) is True

    assert source.published == []  # nothing published: absent, not zero
    assert _sample(metrics, "kajet_sampler_success", collector="queue") == 1


def test_never_succeeded_collector_has_no_age_series():
    source = FakeSource()
    source.fail = True
    metrics = _sampler_metrics(source)
    sampler = metrics.sampler
    assert sampler is not None

    sampler.sample_once(source)

    assert _sample(metrics, "kajet_sampler_age_seconds", collector="queue") is None
    assert _sample(metrics, "kajet_sampler_success", collector="queue") == 0


def test_sampler_thread_runs_on_schedule_and_stops():
    source = FakeSource(interval=0.02)
    metrics = _sampler_metrics(source)
    sampler = metrics.sampler
    assert sampler is not None
    with sampler.running():
        assert source.on_publish.wait(timeout=5)
        assert any(t.name == "kajet-metrics-sampler" for t in threading.enumerate())

    assert not any(t.name == "kajet-metrics-sampler" for t in threading.enumerate())


def test_sampler_without_sources_starts_no_thread():
    metrics = build_metrics(sample_shared=True)
    assert metrics.sampler is not None

    with metrics.sampler.running():
        assert not any(t.name == "kajet-metrics-sampler" for t in threading.enumerate())


def test_sampler_health_is_exposed_with_perf_log_off(monkeypatch):
    """Metrics read typed hooks, never the perf span or parsed log lines."""
    monkeypatch.setattr(perf, "_ENABLED", False)
    source = FakeSource()
    metrics = _sampler_metrics(source)
    assert metrics.sampler is not None
    metrics.sampler.sample_once(source)

    body = metrics.exposition().decode()

    assert 'kajet_sampler_success{collector="queue"} 1.0' in body
    assert "kajet_sampler_age_seconds" in body


# --- /metrics route -----------------------------------------------------------------


@pytest.mark.parametrize("factory", [build_api_app, build_mcp_app, build_app])
def test_every_role_serves_metrics_at_the_root(tmp_path: Path, factory):
    with TestClient(factory(_config(tmp_path))) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("factory", [build_api_app, build_mcp_app, build_app])
def test_metrics_is_not_reachable_under_a_proxied_prefix(tmp_path: Path, factory):
    """Caddy proxies /mcp/* and /api/*, so a route there would be public."""
    with TestClient(factory(_config(tmp_path))) as client:
        assert client.get("/mcp/metrics").status_code in {404, 405}
        assert client.get("/api/metrics").status_code == 404


def test_metrics_route_is_not_in_the_openapi_schema(tmp_path: Path):
    app = build_api_app(_config(tmp_path))

    assert "/metrics" not in app.openapi()["paths"]


def test_exposition_failure_is_contained_to_the_scrape(tmp_path: Path, monkeypatch):
    app = build_api_app(_config(tmp_path))

    def boom(self) -> bytes:
        raise RuntimeError("collector exploded")

    monkeypatch.setattr(Metrics, "exposition", boom)

    with TestClient(app) as client:
        scrape = client.get("/metrics")
        business = client.get("/readyz")

    assert scrape.status_code == 500
    assert "collector exploded" not in scrape.text
    assert business.status_code == 200


def test_scrapes_do_not_emit_http_log_lines(tmp_path: Path, capsys):
    from tests.helpers import entries_named, read_log_entries

    with TestClient(build_api_app(_config(tmp_path))) as client:
        client.get("/metrics")
        capsys.readouterr()  # drop startup noise
        client.get("/metrics")

    assert not entries_named(read_log_entries(capsys), "http")


# --- role defaults ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "mcp_workers", "expected"),
    [
        ("worker", "1", True),
        ("api", "1", False),
        ("mcp", "1", False),
        ("all", "1", True),
        ("all", "2", False),
    ],
)
def test_sample_shared_role_defaults(monkeypatch, role, mcp_workers, expected):
    monkeypatch.setenv("MCP_WORKERS", mcp_workers)

    assert _sample_shared_default(role) is expected


@pytest.mark.parametrize(("raw", "expected"), [(None, None), ("", None), ("1", True), ("0", False)])
def test_sample_shared_env_is_tristate(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("KAJET_METRICS_SAMPLE_SHARED", raising=False)
    else:
        monkeypatch.setenv("KAJET_METRICS_SAMPLE_SHARED", raw)

    assert AppConfig.from_env().metrics_sample_shared is expected


def test_explicit_flag_overrides_the_role_default(tmp_path: Path):
    app = build_api_app(_config(tmp_path, metrics_sample_shared=True))

    assert app.state.resources.metrics.sampler is not None


def test_api_role_does_not_sample_by_default(tmp_path: Path):
    app = build_api_app(_config(tmp_path))

    assert app.state.resources.metrics.sampler is None


# --- worker listener ----------------------------------------------------------------


def test_worker_listener_serves_metrics_and_releases_the_port():
    metrics = build_metrics(sample_shared=False)
    port = _free_port()

    with serve_metrics(metrics, port=port, addr="127.0.0.1") as bound:
        assert bound == port
        response = httpx2.get(f"http://127.0.0.1:{port}/metrics", timeout=5)
        assert response.status_code == 200

    # Shutdown must release the socket: binding the same port again proves it.
    _assert_port_free("127.0.0.1", port)


def test_worker_role_starts_and_stops_the_listener(monkeypatch, tmp_path: Path):
    """main() for role worker must own the listener around the job loop."""
    import sys

    from kajet_turbo import server

    port = _free_port()
    monkeypatch.setattr(sys, "argv", ["kajet-turbo"])
    monkeypatch.setenv("KAJET_ROLE", "worker")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "kajet.db"))
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path / "ws"))
    monkeypatch.setenv("KAJET_METRICS_PORT", str(port))
    monkeypatch.setenv("KAJET_MIGRATE_BRANCHES_ON_START", "0")
    seen: dict = {}

    def fake_run(resources, **_kwargs) -> None:
        seen["scrape"] = httpx2.get(f"http://127.0.0.1:{port}/metrics", timeout=5).status_code
        seen["sampler"] = resources.metrics.sampler is not None

    monkeypatch.setattr(server, "_run_job_worker", fake_run)
    server.main()

    assert seen == {"scrape": 200, "sampler": True}
    _assert_port_free("0.0.0.0", port)


# --- multiprocess directory ---------------------------------------------------------


def test_prepare_multiprocess_dir_creates_and_clears(monkeypatch, tmp_path: Path):
    directory = tmp_path / "prom"
    directory.mkdir()
    (directory / "counter_123.db").write_bytes(b"stale")
    (directory / "keep.txt").write_text("not a metrics file")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(directory))

    prepare_multiprocess_dir()

    assert not list(directory.glob("*.db"))
    assert (directory / "keep.txt").exists()


def test_prepare_multiprocess_dir_creates_missing_directory(monkeypatch, tmp_path: Path):
    directory = tmp_path / "nested" / "prom"
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(directory))

    prepare_multiprocess_dir()

    assert directory.is_dir()


def test_prepare_multiprocess_dir_is_a_noop_without_the_variable(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)

    prepare_multiprocess_dir()  # must not raise or create anything
