"""Real uvicorn supervisor proof for #311: kajet's own `/metrics` in multiprocess mode.

The mechanism was measured on a stand-in app in #310 (docs/specs/metrics-multiprocess.md,
scripts/spike_multiprocess/). This file proves kajet's integration with it: the app's
`Metrics.exposition()`, the exposition-path reaper and the supervisor-owned directory,
under a real `uvicorn.run(workers=2)`.

The supervisor here mirrors `server.main()` for role `api` — `prepare_multiprocess_dir()`
then `uvicorn.run(<factory string>, workers=2)` — but its factory adds a probe counter, a
`livesum` gauge and a `/probe/hit` route to the app's own registry, because no application
metric families exist yet to observe.
"""

import os
import re
import signal
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx2
import pytest

from kajet_turbo.db import Database
from tests.stress.helpers import free_port, terminate, wait_ready

_FACTORY = textwrap.dedent(
    """
    import os

    from prometheus_client import Counter, Gauge
    from starlette.responses import PlainTextResponse

    from kajet_turbo.server import build_api_app


    def build():
        app = build_api_app()
        registry = app.state.resources.metrics.registry
        hits = Counter("probe_hits", "probe hits", registry=registry)
        live = Gauge("probe_live", "live children", registry=registry, multiprocess_mode="livesum")
        live.set(1)  # each child contributes exactly 1

        async def hit(request):
            hits.inc()
            return PlainTextResponse(str(os.getpid()))

        app.add_route("/probe/hit", hit)
        return app
    """
)

_SUPERVISOR = textwrap.dedent(
    """
    import sys

    import uvicorn

    from kajet_turbo.metrics import prepare_multiprocess_dir

    if __name__ == "__main__":
        prepare_multiprocess_dir()
        uvicorn.run(
            "probe_factory:build",
            factory=True,
            host="127.0.0.1",
            port=int(sys.argv[1]),
            workers=2,
            log_level="warning",
        )
    """
)


@contextmanager
def _supervisor(tmp_path: Path, *, prom_dir: Path) -> Iterator[int]:
    (tmp_path / "probe_factory.py").write_text(_FACTORY)
    (tmp_path / "supervisor.py").write_text(_SUPERVISOR)
    db_path = tmp_path / "kajet.db"
    if not db_path.exists():
        Database(str(db_path)).close()  # migrate once, so children do not race Alembic
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, str(tmp_path / "supervisor.py"), str(port)],
        env={
            **os.environ,
            "KAJET_ROLE": "api",
            "PROMETHEUS_MULTIPROC_DIR": str(prom_dir),
            "PYTHONPATH": str(tmp_path),
            "DB_PATH": str(db_path),
            "WORKSPACES_DIR": str(tmp_path / "workspaces"),
            "MCP_BASE_URL": f"http://127.0.0.1:{port}",
            "SECRET_KEY": "stress-test-secret",
            "KAJET_SERVE_SPA": "0",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        wait_ready(port, proc, timeout=40.0)
        yield port
    finally:
        terminate([proc])


def _scrape(port: int) -> str:
    response = httpx2.get(f"http://127.0.0.1:{port}/metrics", timeout=5)
    assert response.status_code == 200
    return response.text


def _value(body: str, name: str) -> float | None:
    match = re.search(rf"^{name}(?:\{{[^}}]*\}})? ([0-9.e+-]+)$", body, re.MULTILINE)
    return float(match.group(1)) if match else None


def _wait_for(check, *, timeout: float = 30.0, message: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.2)
    raise TimeoutError(message)


def _hit_until_two_children(port: int) -> set[int]:
    pids: set[int] = set()
    for _ in range(300):
        pids.add(int(httpx2.get(f"http://127.0.0.1:{port}/probe/hit", timeout=5).text))
        if len(pids) == 2:
            break
    return pids


def test_two_children_share_one_scrape_without_pid_labels(tmp_path: Path):
    with _supervisor(tmp_path, prom_dir=tmp_path / "prom") as port:
        _wait_for(
            lambda: _value(_scrape(port), "probe_live") == 2,
            message="both children never reported in",
        )
        for _ in range(20):
            httpx2.get(f"http://127.0.0.1:{port}/probe/hit", timeout=5)

        body = _scrape(port)

    assert _value(body, "probe_hits_total") == 20  # summed across children, not per PID
    assert "pid=" not in body
    # Multiprocess exposition has no `_created` series; single-process would (§5).
    assert "_created" not in body


def test_reaper_removes_a_killed_childs_live_gauges_but_keeps_counters(tmp_path: Path):
    with _supervisor(tmp_path, prom_dir=tmp_path / "prom") as port:
        _wait_for(
            lambda: _value(_scrape(port), "probe_live") == 2,
            message="both children never reported in",
        )
        pids = _hit_until_two_children(port)
        assert len(pids) == 2, "requests never reached both children"
        hits_before = _value(_scrape(port), "probe_hits_total")
        assert hits_before is not None

        victim = next(iter(pids))
        os.kill(victim, signal.SIGKILL)

        # uvicorn respawns the child. Only once a *new* pid serves traffic has the
        # replacement reported in as a fresh `livesum` contributor; before that, the
        # dead child's file and the surviving child's add up to two by coincidence.
        # Without the reaper the total then reads 3 (dead + survivor + replacement).
        def replaced() -> bool:
            try:
                response = httpx2.get(f"http://127.0.0.1:{port}/probe/hit", timeout=2)
            except httpx2.TransportError:
                return False
            return int(response.text) not in pids

        _wait_for(replaced, message="killed child was never replaced")
        _wait_for(
            lambda: _value(_scrape(port), "probe_live") == 2,
            message="live gauge never settled back to two children",
        )
        body = _scrape(port)

    hits_after = _value(body, "probe_hits_total")
    assert hits_after is not None
    assert hits_after >= hits_before  # counters survive the restart


def test_restart_over_stale_files_starts_counters_at_zero(tmp_path: Path):
    prom_dir = tmp_path / "prom"
    with _supervisor(tmp_path, prom_dir=prom_dir) as port:
        for _ in range(5):
            httpx2.get(f"http://127.0.0.1:{port}/probe/hit", timeout=5)
        assert _value(_scrape(port), "probe_hits_total") == 5

    assert list(prom_dir.glob("*.db")), "first run left nothing to be stale"

    with _supervisor(tmp_path, prom_dir=prom_dir) as port:
        assert _value(_scrape(port), "probe_hits_total") in (None, 0)


@pytest.mark.parametrize("path", ["/mcp/metrics", "/api/metrics"])
def test_prefixed_paths_are_not_routed_by_the_app(tmp_path: Path, path: str):
    with _supervisor(tmp_path, prom_dir=tmp_path / "prom") as port:
        assert httpx2.get(f"http://127.0.0.1:{port}{path}", timeout=5).status_code == 404
