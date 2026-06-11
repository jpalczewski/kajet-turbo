# Python 3.14t + Async Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop blocking the event loop (dispatch all sync DB/git work to a bounded threadpool), switch the runtime to free-threaded Python 3.14t, add an epoch-invalidated TTL cache, and prove the effect with before/after benchmarks.

**Architecture:** Services and repositories stay synchronous. A new `run_sync()` helper (anyio.to_thread + dedicated `CapacityLimiter(10)`) is the single async/sync boundary, used by async REST endpoints and MCP tools. Git mutations get a per-workspace `threading.Lock`. A thread-safe TTL cache with per-workspace epochs fronts search and history. Benchmarks run as a 3-way matrix: baseline/3.14 → refactor/3.14 → refactor/3.14t.

**Tech Stack:** FastAPI, FastMCP, SQLModel (sync), dulwich, anyio, cachetools, httpx (bench), uv-managed CPython 3.14t.

**Spec:** `docs/superpowers/specs/2026-06-11-python-314t-async-refactor-design.md`

**Deviation from spec (decided during planning):** the spec says MCP tools become `def` so FastMCP runs them in its threadpool. That is impossible: every tool calls `await ctx.get_state(...)` (FastMCP's Context API is async-only, see `src/kajet_turbo/mcp/workspaces.py:12-20`). Tools therefore stay `async def` and instead wrap each sync service call in `await run_sync(...)` — same effect (work leaves the event loop). Task 5 updates the spec accordingly.

---

### Task 1: Benchmark harness

**Files:**
- Create: `scripts/bench.py`
- Create: `scripts/bench_report.py`
- Create: `tests/test_bench_report.py`
- Modify: `pyproject.toml` (dev deps)

- [ ] **Step 1: Add dev dependencies**

In `pyproject.toml`, change the dev group to:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.9",
    "httpx>=0.27",
]
```

Run: `uv sync`
Expected: resolves and installs without error.

- [ ] **Step 2: Write failing test for the report generator**

Create `tests/test_bench_report.py`:

```python
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from bench_report import build_report  # noqa: E402


def _result(label: str, p95: float, rps: float) -> dict:
    return {
        "label": label,
        "python": "3.14.2",
        "free_threading": False,
        "scenarios": {
            "note_html@c10": {"latency_ms": {"p50": 1.0, "p95": p95, "p99": 9.0,
                                             "mean": 2.0, "count": 300},
                              "rps": rps, "errors": 0},
        },
    }


def test_build_report_compares_labels():
    report = build_report([_result("baseline", 8.0, 120.0), _result("after", 4.0, 240.0)])
    assert "note_html@c10" in report
    assert "baseline" in report and "after" in report
    assert "8.0" in report and "4.0" in report
    assert "+100.0%" in report  # rps delta vs first label


def test_build_report_roundtrips_json(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(_result("x", 1.0, 10.0)))
    report = build_report([json.loads(p.read_text())])
    assert "x" in report
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_bench_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'bench_report'`

- [ ] **Step 4: Implement `scripts/bench_report.py`**

```python
"""Build a markdown comparison table from bench.py JSON result files.

Usage: uv run python scripts/bench_report.py results/a.json results/b.json > report.md
The first file is the baseline for delta columns.
"""
import json
import sys


def build_report(results: list[dict]) -> str:
    labels = [r["label"] for r in results]
    scenario_names: list[str] = []
    for r in results:
        for name in r["scenarios"]:
            if name not in scenario_names:
                scenario_names.append(name)

    lines = ["# Benchmark report", ""]
    lines.append("| run | python | free-threading |")
    lines.append("|---|---|---|")
    for r in results:
        lines.append(f"| {r['label']} | {r['python']} | {r['free_threading']} |")
    lines.append("")

    header = "| scenario | metric | " + " | ".join(labels) + " | delta vs first |"
    sep = "|---" * (len(labels) + 3) + "|"
    lines += [header, sep]
    for name in scenario_names:
        for metric, getter in (
            ("p50 ms", lambda s: s["latency_ms"]["p50"]),
            ("p95 ms", lambda s: s["latency_ms"]["p95"]),
            ("p99 ms", lambda s: s["latency_ms"]["p99"]),
            ("rps", lambda s: s["rps"]),
        ):
            cells, values = [], []
            for r in results:
                s = r["scenarios"].get(name)
                cells.append("–" if s is None else f"{getter(s)}")
                values.append(None if s is None else getter(s))
            delta = "–"
            if values[0] and values[-1] is not None:
                pct = (values[-1] - values[0]) / values[0] * 100
                delta = f"{pct:+.1f}%"
            lines.append(f"| {name} | {metric} | " + " | ".join(cells) + f" | {delta} |")
    return "\n".join(lines)


def main() -> None:
    results = [json.loads(open(p).read()) for p in sys.argv[1:]]
    print(build_report(results))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_bench_report.py -v`
Expected: 2 passed

- [ ] **Step 6: Implement `scripts/bench.py`**

No automated test (it is a measurement tool that needs a live server); verified manually in Step 7. Complete content:

```python
"""Benchmark harness for kajet-turbo.

Spawns the server as a subprocess on a temp DB + temp workspaces dir, seeds
notes over HTTP, runs HTTP scenarios at several concurrency levels, then (with
the server stopped) runs in-process threaded search scenarios against the same
DB. Writes one JSON result file.

Usage:
    uv run python scripts/bench.py --label baseline-py314 \
        --out docs/benchmarks/baseline-py314.json
"""
import argparse
import asyncio
import json
import random
import statistics
import subprocess
import sys
import tempfile
import time
import os
from pathlib import Path

import httpx

ADMIN_EMAIL = "bench@local"
ADMIN_PASSWORD = "bench-password"
WS = "bench"
PORT = 8765
WORDS = [
    "projekt", "klient", "spotkanie", "notatka", "pomysl", "budzet", "raport",
    "analiza", "wdrozenie", "testy", "architektura", "baza", "wyszukiwanie",
    "frontend", "backend", "python", "sqlite", "asyncio", "watki", "cache",
]
QUERIES = ["projekt klient", "raport budzet", "sqlite cache", "testy backend",
           "architektura baza", "python asyncio", "notatka spotkanie",
           "frontend wdrozenie", "analiza raport", "watki python"]


def make_content(rng: random.Random, paragraphs: int = 6) -> str:
    return "\n\n".join(" ".join(rng.choices(WORDS, k=60)) for _ in range(paragraphs))


def percentiles(latencies_ms: list[float]) -> dict:
    xs = sorted(latencies_ms)

    def pct(p: float) -> float:
        return round(xs[min(len(xs) - 1, max(0, round(p / 100 * (len(xs) - 1))))], 2)

    return {"p50": pct(50), "p95": pct(95), "p99": pct(99),
            "mean": round(statistics.fmean(xs), 2), "count": len(xs)}


def rss_mb(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.strip()
    return round(int(out) / 1024, 1) if out else 0.0


def spawn_server(tmp: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "DB_PATH": str(tmp / "bench.db"),
        "WORKSPACES_DIR": str(tmp / "workspaces"),
        "KAJET_ADMIN_EMAIL": ADMIN_EMAIL,
        "KAJET_ADMIN_PASSWORD": ADMIN_PASSWORD,
        "MCP_PORT": str(PORT),
        "MCP_HOST": "127.0.0.1",
        "LOG_LEVEL": "WARNING",
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", "from kajet_turbo.server import main; main()"], env=env
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{PORT}/api/session", timeout=1)
            return proc
        except httpx.HTTPError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("server did not become ready in 30s")


async def login(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/login",
                          json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    r.raise_for_status()


async def seed(client: httpx.AsyncClient, n_notes: int, rng: random.Random) -> tuple[list[str], dict]:
    r = await client.post("/api/workspaces", json={"name": WS})
    r.raise_for_status()
    note_ids: list[str] = []
    latencies: list[float] = []
    sem = asyncio.Semaphore(4)

    async def create(i: int) -> None:
        async with sem:
            t0 = time.perf_counter()
            r = await client.post(
                f"/api/workspaces/{WS}/notes",
                json={"title": f"Nota {i}", "content": make_content(rng),
                      "tags": [rng.choice(WORDS)], "folder": ""},
            )
            latencies.append((time.perf_counter() - t0) * 1000)
            r.raise_for_status()
            note_ids.append(r.json()["note_id"])

    t0 = time.perf_counter()
    await asyncio.gather(*(create(i) for i in range(n_notes)))
    wall = time.perf_counter() - t0
    return note_ids, {"latency_ms": percentiles(latencies),
                      "rps": round(n_notes / wall, 1), "errors": 0}


async def run_http_scenario(client, make_request, total: int, concurrency: int) -> dict:
    latencies: list[float] = []
    errors = 0
    sem = asyncio.Semaphore(concurrency)

    async def one(i: int) -> None:
        nonlocal errors
        async with sem:
            t0 = time.perf_counter()
            try:
                r = await make_request(i)
                if r.status_code >= 400:
                    errors += 1
            except httpx.HTTPError:
                errors += 1
            latencies.append((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    await asyncio.gather(*(one(i) for i in range(total)))
    wall = time.perf_counter() - t0
    return {"latency_ms": percentiles(latencies),
            "rps": round(len(latencies) / wall, 1), "errors": errors}


async def http_phase(n_notes: int, server_pid: int) -> tuple[dict, float]:
    rng = random.Random(42)
    scenarios: dict[str, dict] = {}
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{PORT}", timeout=60) as client:
        await login(client)
        note_ids, seed_stats = await seed(client, n_notes, rng)
        scenarios["seed_write@c4"] = seed_stats

        def html(i):
            return client.get(f"/api/workspaces/{WS}/notes/{note_ids[i % len(note_ids)]}/html")

        def listing(i):
            return client.get(f"/api/workspaces/{WS}/notes")

        def history(i):
            return client.get(f"/api/workspaces/{WS}/notes/{note_ids[i % len(note_ids)]}/history")

        def patch(i):
            return client.patch(
                f"/api/workspaces/{WS}/notes/{note_ids[i % len(note_ids)]}",
                json={"content": make_content(rng)})

        def mixed(i):
            return patch(i) if i % 5 == 0 else html(i)

        for conc in (1, 10, 50):
            scenarios[f"note_html@c{conc}"] = await run_http_scenario(client, html, 300, conc)
            scenarios[f"note_list@c{conc}"] = await run_http_scenario(client, listing, 150, conc)
            scenarios[f"history@c{conc}"] = await run_http_scenario(client, history, 150, conc)
        for conc in (1, 10):
            scenarios[f"note_patch@c{conc}"] = await run_http_scenario(client, patch, 60, conc)
        scenarios["mixed_80_20@c20"] = await run_http_scenario(client, mixed, 300, 20)
        rss = rss_mb(server_pid)
    return scenarios, rss


def inproc_search_phase(tmp: Path) -> dict:
    """Threaded search against the seeded DB, server stopped. Measures FTS/vec
    C-code scaling across real threads (the free-threading payoff)."""
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor

    os.environ["DB_PATH"] = str(tmp / "bench.db")
    os.environ["WORKSPACES_DIR"] = str(tmp / "workspaces")
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.notes import NoteRepository
    from kajet_turbo.services.notes import NoteService

    owner_id = sqlite3.connect(str(tmp / "bench.db")).execute(
        'SELECT id FROM "user" LIMIT 1').fetchone()[0]
    db = Database()
    svc = NoteService(NoteRepository(db.engine))
    results: dict[str, dict] = {}
    total = 200
    for threads in (1, 4, 8):
        latencies: list[float] = []

        def one(i: int) -> None:
            t0 = time.perf_counter()
            svc.search(QUERIES[i % len(QUERIES)], [WS], owner_id=owner_id, limit=10)
            latencies.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=threads) as ex:
            list(ex.map(one, range(total)))
        wall = time.perf_counter() - t0
        results[f"search_inproc@t{threads}"] = {
            "latency_ms": percentiles(latencies),
            "rps": round(total / wall, 1), "errors": 0}

    from kajet_turbo.workspace import workspace_path
    t0 = time.perf_counter()
    reindexed = svc.reindex(WS, owner_id=owner_id,
                            ws_path=workspace_path(WS, user_id=owner_id))
    wall = time.perf_counter() - t0
    results["reindex_total"] = {
        "latency_ms": percentiles([wall * 1000]),
        "rps": round(reindexed["count"] / wall, 1), "errors": 0}
    db.close()
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--notes", type=int, default=500)
    args = ap.parse_args()

    free_threading = bool(getattr(sys, "_is_gil_enabled", lambda: True)()) is False

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        proc = spawn_server(tmp)
        try:
            scenarios, rss = asyncio.run(http_phase(args.notes, proc.pid))
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        scenarios.update(inproc_search_phase(tmp))

    result = {
        "label": args.label,
        "python": sys.version.split()[0],
        "free_threading": free_threading,
        "notes": args.notes,
        "server_rss_mb": rss,
        "scenarios": scenarios,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(f"wrote {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Smoke-run the harness (small N)**

Run: `uv run python scripts/bench.py --label smoke --out /tmp/smoke.json --notes 30 && uv run python scripts/bench_report.py /tmp/smoke.json | head -20`
Expected: `wrote /tmp/smoke.json` and a markdown table on stdout. All scenarios show `"errors": 0`. If errors > 0, debug before continuing (check server stderr).

- [ ] **Step 8: Commit**

```bash
git add scripts/bench.py scripts/bench_report.py tests/test_bench_report.py pyproject.toml uv.lock
git commit -m "feat: add benchmark harness for before/after measurements"
```

---

### Task 2: Baseline measurement (current code, Python 3.14)

**Files:**
- Create: `docs/benchmarks/baseline-py314.json`

- [ ] **Step 1: Confirm the working tree has no refactor changes yet**

Run: `git status --porcelain src/`
Expected: empty output (benchmarks must measure the pre-refactor code).

- [ ] **Step 2: Run the baseline benchmark**

Run: `uv run python scripts/bench.py --label baseline-py314 --out docs/benchmarks/baseline-py314.json`
Expected: `wrote docs/benchmarks/baseline-py314.json`, errors 0 in all scenarios. Takes a few minutes. Close other heavy apps for measurement stability.

- [ ] **Step 3: Sanity-check the numbers**

Run: `uv run python scripts/bench_report.py docs/benchmarks/baseline-py314.json`
Expected: table renders; `note_html@c50` p95 should be visibly worse than `@c1` (event-loop blocking shows up under concurrency). Note the `mixed_80_20@c20` p99 — this is the headline "before" number.

- [ ] **Step 4: Commit**

```bash
git add docs/benchmarks/baseline-py314.json
git commit -m "bench: record baseline on Python 3.14 before refactor"
```

---

### Task 3: `run_sync` concurrency helper

**Files:**
- Create: `src/kajet_turbo/concurrency.py`
- Create: `tests/test_concurrency.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_concurrency.py`:

```python
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
        anyio.from_thread.run_sync(lambda: None)  # yield a bit
        import time
        time.sleep(0.05)
        with lock:
            active -= 1

    async with anyio.create_task_group() as tg:
        for _ in range(30):
            tg.start_soon(run_sync, work)
    assert peak <= 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_concurrency.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kajet_turbo.concurrency'`

- [ ] **Step 3: Implement `src/kajet_turbo/concurrency.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_concurrency.py -v`
Expected: 3 passed. If `test_run_sync_bounded_concurrency` is flaky, drop the `anyio.from_thread.run_sync` line (it was only there to encourage interleaving) and keep the `time.sleep`.

- [ ] **Step 5: Commit**

```bash
git add src/kajet_turbo/concurrency.py tests/test_concurrency.py
git commit -m "feat: add run_sync helper with dedicated capacity limiter"
```

---

### Task 4: Stop blocking the event loop in async REST endpoints

**Files:**
- Modify: `src/kajet_turbo/api/workspaces.py`

Sync `def` GET endpoints already run in Starlette's threadpool — leave them. Only `async def` endpoints change. `get_session_user`/`has_access` are single indexed SELECTs (<1 ms) and stay inline by design.

- [ ] **Step 1: Add import**

At the top of `src/kajet_turbo/api/workspaces.py`, after the `dependencies` import (line 46):

```python
from kajet_turbo.concurrency import run_sync
```

- [ ] **Step 2: Wrap each blocking call in the six async endpoints**

In `api_create_workspace` (line 83), replace `ws_service.create(name, user["id"])` with:

```python
        await run_sync(ws_service.create, name, user["id"])
```

In `api_create_folder` (lines 212-216), replace the `try` block body `GitRepository(ws_path).commit_file(relative, f"folder: add {path}")` with:

```python
            await run_sync(
                lambda: GitRepository(ws_path).commit_file(relative, f"folder: add {path}")
            )
```

In `api_create_note` (line 248), replace `result = note_service.save(user["id"], name, ws_path, title, content, tags, folder=folder)` with:

```python
        result = await run_sync(
            note_service.save, user["id"], name, ws_path, title, content, tags, folder=folder
        )
```

In `api_update_note` (lines 280-288), replace the `note_service.update(...)` call with:

```python
        result = await run_sync(
            note_service.update,
            note_id,
            owner_id=user["id"],
            ws_path=ws_path,
            title=title,
            content=content,
            tags=tags,
            folder=folder,
        )
```

In `api_delete_note` (line 310), replace `note_service.delete(note_id, owner_id=user["id"], ws_path=ws_path)` with:

```python
        await run_sync(note_service.delete, note_id, owner_id=user["id"], ws_path=ws_path)
```

In `api_restore_note_version` (line 448), replace `result = note_service.restore_version(note_id, sha, owner_id=user["id"], ws_path=ws_path)` with:

```python
        result = await run_sync(
            note_service.restore_version, note_id, sha, owner_id=user["id"], ws_path=ws_path
        )
```

- [ ] **Step 3: Run the API test suite**

Run: `uv run pytest tests/test_api_workspaces.py -v`
Expected: all pass (behavior unchanged — exceptions propagate out of `run_sync` exactly like direct calls, so the existing `except` clauses still fire).

- [ ] **Step 4: Run full suite**

Run: `uv run pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/kajet_turbo/api/workspaces.py
git commit -m "fix: dispatch blocking service calls to threadpool in async REST endpoints"
```

---

### Task 5: Stop blocking the event loop in MCP tools

**Files:**
- Modify: `src/kajet_turbo/mcp/notes.py`
- Modify: `src/kajet_turbo/mcp/workspaces.py`
- Modify: `docs/superpowers/specs/2026-06-11-python-314t-async-refactor-design.md` (record deviation)

Tools stay `async def` (Context API is async-only); each sync service call gets wrapped.

- [ ] **Step 1: `mcp/notes.py` — add import and wrap all 10 service calls**

Add after line 8 (`from kajet_turbo.services.notes import NoteService`):

```python
from kajet_turbo.concurrency import run_sync
```

Replace each call site (left = current, right = new):

| line | current | new |
|---|---|---|
| 31 | `result = note_service.save(owner_id, ws_name, ws_path, title, content, tags or [], folder=folder)` | `result = await run_sync(note_service.save, owner_id, ws_name, ws_path, title, content, tags or [], folder=folder)` |
| 44 | `result = note_service.get_with_content(note_id, owner_id=owner_id, ws_path=ws_path)` | `result = await run_sync(note_service.get_with_content, note_id, owner_id=owner_id, ws_path=ws_path)` |
| 66-67 | `result = note_service.update(note_id, owner_id=owner_id, ws_path=ws_path, title=title, content=content, tags=tags, folder=folder)` | `result = await run_sync(note_service.update, note_id, owner_id=owner_id, ws_path=ws_path, title=title, content=content, tags=tags, folder=folder)` |
| 83 | `note_service.delete(note_id, owner_id=owner_id, ws_path=ws_path)` | `await run_sync(note_service.delete, note_id, owner_id=owner_id, ws_path=ws_path)` |
| 103 | `notes = note_service.list(ws_name, owner_id=owner_id, tags=tags or None, limit=limit, folder=folder)` | `notes = await run_sync(note_service.list, ws_name, owner_id=owner_id, tags=tags or None, limit=limit, folder=folder)` |
| 123 | `workspaces = workspace_service.list_accessible(real_user_id)` | `workspaces = await run_sync(workspace_service.list_accessible, real_user_id)` |
| 126 | `results = note_service.search(query, workspaces, owner_id=owner_id, limit=limit)` | `results = await run_sync(note_service.search, query, workspaces, owner_id=owner_id, limit=limit)` |
| 138 | `result = note_service.reindex(ws_name, owner_id=owner_id, ws_path=ws_path)` | `result = await run_sync(note_service.reindex, ws_name, owner_id=owner_id, ws_path=ws_path)` |
| 152 | `entries = note_service.get_history(note_id, owner_id=owner_id, ws_path=ws_path, limit=limit)` | `entries = await run_sync(note_service.get_history, note_id, owner_id=owner_id, ws_path=ws_path, limit=limit)` |
| 168 | `version = note_service.get_version(note_id, sha, owner_id=owner_id, ws_path=ws_path)` | `version = await run_sync(note_service.get_version, note_id, sha, owner_id=owner_id, ws_path=ws_path)` |
| 184 | `result = note_service.restore_version(note_id, sha, owner_id=owner_id, ws_path=ws_path)` | `result = await run_sync(note_service.restore_version, note_id, sha, owner_id=owner_id, ws_path=ws_path)` |

- [ ] **Step 2: `mcp/workspaces.py` — wrap the four DB-touching calls**

Add after line 9 (`from kajet_turbo.services.workspaces import WorkspaceService`):

```python
from kajet_turbo.concurrency import run_sync
```

| line | current | new |
|---|---|---|
| 46 (in `list_workspaces`) | `user_id, err = _resolve_user()` | `user_id, err = await run_sync(_resolve_user)` |
| 49 | `return json.dumps(workspace_service.list_accessible(user_id))` | `return json.dumps(await run_sync(workspace_service.list_accessible, user_id))` |
| 56 (in `activate_workspace`) | `user_id, err = _resolve_user()` | `user_id, err = await run_sync(_resolve_user)` |
| 59 | `available = workspace_service.list_accessible(user_id)` | `available = await run_sync(workspace_service.list_accessible, user_id)` |
| 76 (in `create_workspace`) | `user_id, err = _resolve_user()` | `user_id, err = await run_sync(_resolve_user)` |
| 80 | `workspace_service.create(name, user_id)` | `await run_sync(workspace_service.create, name, user_id)` |

Note: `_resolve_user` calls `get_access_token()` (FastMCP context-var read — works from worker threads because AnyIO propagates contextvars) plus a DB query, hence the wrap. `get_active_workspace` only computes a path — stays as is.

- [ ] **Step 3: Run MCP/server tests**

Run: `uv run pytest tests/test_server.py tests/test_log.py -v`
Expected: all pass. If `get_access_token()` fails inside the worker thread (contextvar not propagated by the installed anyio version), revert the three `_resolve_user` wraps to direct calls — it is a single SELECT — and leave the rest wrapped.

- [ ] **Step 4: Run full suite**

Run: `uv run pytest`
Expected: all pass.

- [ ] **Step 5: Record the spec deviation**

In `docs/superpowers/specs/2026-06-11-python-314t-async-refactor-design.md`, section "2. Async/sync boundary", replace the bullet:

```markdown
- MCP: all tools change from `async def` to **`def`** — FastMCP runs sync tools
  in its threadpool automatically. Minimal diff.
```

with:

```markdown
- MCP: tools stay `async def` (FastMCP's `ctx.get_state` API is async-only);
  every sync service call is wrapped in `await run_sync(...)` instead — same
  effect, work leaves the event loop.
```

- [ ] **Step 6: Commit**

```bash
git add src/kajet_turbo/mcp/notes.py src/kajet_turbo/mcp/workspaces.py docs/superpowers/specs/2026-06-11-python-314t-async-refactor-design.md
git commit -m "fix: dispatch blocking service calls to threadpool in MCP tools"
```

---

### Task 6: Per-workspace git lock

**Files:**
- Modify: `src/kajet_turbo/repositories/git.py`
- Test: `tests/test_git_repository.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/test_git_repository.py`:

```python
def test_parallel_commits_to_same_repo_do_not_corrupt(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from dulwich.repo import Repo

    from kajet_turbo.repositories.git import GitRepository

    GitRepository.init(str(tmp_path))

    def write_and_commit(i: int) -> None:
        (tmp_path / f"note-{i}.md").write_text(f"content {i}")
        GitRepository(str(tmp_path)).commit_file(f"note-{i}.md", f"add {i}")

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(write_and_commit, range(16)))

    commits = list(Repo(str(tmp_path)).get_walker())
    assert len(commits) == 16
```

- [ ] **Step 2: Run it to observe the race**

Run: `uv run pytest tests/test_git_repository.py::test_parallel_commits_to_same_repo_do_not_corrupt -v`
Expected: FAIL (lost commits: fewer than 16 in history, or a `GitError` from a corrupted index). Dulwich's index add+commit is not atomic across threads. **Note:** this race is timing-dependent — if it passes, re-run up to 3×; if it still passes, proceed anyway (the lock is cheap insurance) and note it in the commit message.

- [ ] **Step 3: Implement the keyed lock**

In `src/kajet_turbo/repositories/git.py`, add after the `COMMITTER` constant (line 8):

```python
import threading

_REPO_LOCKS: dict[str, threading.Lock] = {}
_REPO_LOCKS_GUARD = threading.Lock()


def _repo_lock(workspace_path: str) -> threading.Lock:
    key = str(Path(workspace_path).resolve())
    with _REPO_LOCKS_GUARD:
        lock = _REPO_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _REPO_LOCKS[key] = lock
        return lock
```

(move the `import threading` up to the stdlib import block at the top of the file).

Wrap the bodies of the three mutating methods — `commit_file` (line 28), `delete_file` (line 42), `rename_file` (line 57) — in the lock. Pattern for `commit_file`; apply identically to the other two (entire existing `try/except` body indented under the `with`):

```python
    def commit_file(self, relative_path: str, message: str) -> None:
        with _repo_lock(self._workspace_path):
            try:
                if not Path(self._workspace_path, relative_path).exists():
                    raise GitError(f"File not found: {relative_path}")
                porcelain.add(self._workspace_path, paths=[relative_path])
                porcelain.commit(
                    self._workspace_path,
                    message=message.encode(),
                    author=COMMITTER,
                    committer=COMMITTER,
                )
            except Exception as e:
                raise GitError(str(e)) from e
```

Read methods (`last_commit_time`, `file_history`, `file_content_at_commit`) stay lock-free.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_git_repository.py -v`
Expected: all pass, including the new parallel test.

- [ ] **Step 5: Commit**

```bash
git add src/kajet_turbo/repositories/git.py tests/test_git_repository.py
git commit -m "fix: serialize git mutations per workspace with keyed lock"
```

---

### Task 7: Runtime visibility + concurrency stress test

**Files:**
- Modify: `src/kajet_turbo/server.py:16-25`
- Create: `tests/test_concurrency_stress.py`

- [ ] **Step 1: Log runtime/GIL state at startup**

In `src/kajet_turbo/server.py`: add `import sys` to the imports (line 1 block) and `from kajet_turbo.log import LoggingMiddleware, setup_logging` already exists — extend it to also import `logger`:

```python
from kajet_turbo.log import LoggingMiddleware, logger, setup_logging
```

In `_app_lifespan`, before the admin-user block (line 18), add:

```python
    gil_enabled = sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True
    logger.info("runtime", python=sys.version.split()[0], free_threading=not gil_enabled)
```

- [ ] **Step 2: Write the stress test**

Create `tests/test_concurrency_stress.py`:

```python
"""Parallel save/search/history on one workspace — catches git races,
SQLite pool exhaustion and (later) cache races under real threads."""
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from kajet_turbo.db import Database
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.notes import NoteService

WS = "stress"
OWNER = "user-stress"


@pytest.fixture()
def svc(tmp_path):
    db = Database(db_path=str(tmp_path / "stress.db"))
    service = NoteService(NoteRepository(db.engine))
    yield service, str(tmp_path / "ws")
    db.close()


def test_parallel_save_search_history(svc, tmp_path):
    service, ws_path = svc
    Path(ws_path).mkdir()
    GitRepository.init(ws_path)
    seed = service.save(OWNER, WS, ws_path, "Seed", "treść początkowa", [])

    errors: list[Exception] = []

    def save(i: int) -> None:
        try:
            service.save(OWNER, WS, ws_path, f"Nota {i}", f"treść {i}", ["tag"])
        except Exception as e:  # noqa: BLE001 — collect everything
            errors.append(e)

    def search(i: int) -> None:
        try:
            service.search("treść", [WS], owner_id=OWNER, limit=10)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def history(i: int) -> None:
        try:
            service.get_history(seed["note_id"], owner_id=OWNER, ws_path=ws_path)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = []
        for i in range(20):
            futures.append(ex.submit(save, i))
            futures.append(ex.submit(search, i))
            futures.append(ex.submit(history, i))
        for f in futures:
            f.result()

    assert errors == []
    notes = service.list(WS, owner_id=OWNER, limit=100)
    assert len(notes) == 21  # seed + 20 parallel saves
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_concurrency_stress.py -v`
Expected: PASS (git lock from Task 6 makes parallel saves safe; pool 5+5 absorbs 10 workers).

- [ ] **Step 4: Audit shared singletons for mutable state**

Read `src/kajet_turbo/dependencies.py` and each class it instantiates (`NoteService`, `WorkspaceService`, all repositories, `Database`). Confirm none of them mutate instance attributes after `__init__` (they should only hold the engine/repo references). If any mutable shared state is found, report it before continuing — it needs a lock or a redesign, not a silent fix.

- [ ] **Step 5: Run full suite and commit**

Run: `uv run pytest`
Expected: all pass.

```bash
git add src/kajet_turbo/server.py tests/test_concurrency_stress.py
git commit -m "feat: log GIL state at startup, add thread-safety stress test"
```

---

### Task 8: Epoch-invalidated TTL cache for search and history

**Files:**
- Create: `src/kajet_turbo/cache.py`
- Create: `tests/test_cache.py`
- Modify: `src/kajet_turbo/services/notes.py`
- Modify: `src/kajet_turbo/dependencies.py:21`
- Modify: `pyproject.toml` (add cachetools)

- [ ] **Step 1: Add dependency**

In `pyproject.toml` `[project].dependencies`, add:

```toml
    "cachetools>=5.5",
```

Run: `uv sync`
Expected: installs cachetools.

- [ ] **Step 2: Write failing tests**

Create `tests/test_cache.py`:

```python
from kajet_turbo.cache import WorkspaceCache
from kajet_turbo.services.notes import NoteService


def test_epoch_starts_at_zero_and_bumps():
    c = WorkspaceCache()
    assert c.epoch("ws", "u") == 0
    c.bump("ws", "u")
    assert c.epoch("ws", "u") == 1
    assert c.epoch("other", "u") == 0


def test_get_put_roundtrip_and_miss():
    c = WorkspaceCache()
    assert c.get(("k",)) is None
    c.put(("k",), [1, 2])
    assert c.get(("k",)) == [1, 2]


def test_ttl_expiry():
    clock = [0.0]
    c = WorkspaceCache(ttl=10, timer=lambda: clock[0])
    c.put(("k",), 1)
    assert c.get(("k",)) == 1
    clock[0] = 11.0
    assert c.get(("k",)) is None


class FakeRepo:
    def __init__(self) -> None:
        self.calls = 0

    def hybrid_search(self, query, ws, owner_id, limit):
        self.calls += 1
        return [{"note_id": "n1", "title": "t"}]


def test_search_is_cached_and_epoch_invalidates():
    cache = WorkspaceCache()
    repo = FakeRepo()
    svc = NoteService(repo, cache=cache)

    r1 = svc.search("q", ["ws"], owner_id="u")
    r2 = svc.search("q", ["ws"], owner_id="u")
    assert r1 == r2
    assert repo.calls == 1  # second call served from cache

    cache.bump("ws", "u")
    svc.search("q", ["ws"], owner_id="u")
    assert repo.calls == 2  # epoch change = new key = recompute


def test_search_without_cache_always_hits_repo():
    repo = FakeRepo()
    svc = NoteService(repo, cache=None)
    svc.search("q", ["ws"], owner_id="u")
    svc.search("q", ["ws"], owner_id="u")
    assert repo.calls == 2
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kajet_turbo.cache'`

- [ ] **Step 4: Implement `src/kajet_turbo/cache.py`**

```python
"""Thread-safe TTL cache with per-workspace epochs.

Every write to a workspace bumps its epoch; the epoch is part of every cache
key, so all entries for that workspace become unreachable at once (and expire
via TTL). No explicit invalidation, no races.

Limitation (by design, see spec): per-process. With MCP_WORKERS>1 each process
caches independently; TTL bounds staleness caused by writes in sibling
processes.
"""
import os
import threading
from collections.abc import Callable

from cachetools import TTLCache


def cache_enabled() -> bool:
    return os.getenv("KAJET_CACHE", "1") != "0"


class WorkspaceCache:
    def __init__(self, maxsize: int = 2048, ttl: float = 300.0,
                 timer: Callable[[], float] | None = None) -> None:
        kwargs = {"timer": timer} if timer is not None else {}
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl, **kwargs)
        self._epochs: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def epoch(self, ws_name: str, owner_id: str) -> int:
        with self._lock:
            return self._epochs.get((ws_name, owner_id), 0)

    def bump(self, ws_name: str, owner_id: str) -> None:
        with self._lock:
            key = (ws_name, owner_id)
            self._epochs[key] = self._epochs.get(key, 0) + 1

    def get(self, key: tuple):
        with self._lock:
            return self._cache.get(key)

    def put(self, key: tuple, value) -> None:
        with self._lock:
            self._cache[key] = value
```

- [ ] **Step 5: Integrate into `NoteService`**

In `src/kajet_turbo/services/notes.py`:

Constructor (lines 16-17) becomes:

```python
    def __init__(self, note_repo: NoteRepository, cache: "WorkspaceCache | None" = None) -> None:
        self._repo = note_repo
        self._cache = cache
```

with import added at the top:

```python
from kajet_turbo.cache import WorkspaceCache
```

(plain import, not TYPE_CHECKING — it is used at runtime by `dependencies.py` anyway and has no import cycle: `cache.py` imports nothing from kajet_turbo).

`search()` (lines 159-173) becomes:

```python
    def search(
        self,
        query: str,
        workspaces: list[str],
        owner_id: str,
        limit: int = 10,
    ) -> list[dict]:
        key = None
        if self._cache is not None:
            epochs = tuple(self._cache.epoch(ws, owner_id) for ws in workspaces)
            key = ("search", owner_id, tuple(workspaces), epochs, query, limit)
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        per_ws_limit = limit * 3 if len(workspaces) > 1 else limit
        results = []
        for ws in workspaces:
            hits = self._repo.hybrid_search(query, ws, owner_id, limit=per_ws_limit)
            results.extend(hits)
        results = results[:limit]
        if key is not None:
            self._cache.put(key, results)
        logger.info("search_performed", query_len=len(query), results=len(results), ws_count=len(workspaces))
        return results
```

`get_history()` (lines 200-206) becomes:

```python
    def get_history(self, note_id: str, owner_id: str, ws_path: str, limit: int = 50) -> list[dict]:
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        key = None
        if self._cache is not None:
            key = ("history", note_id, owner_id,
                   self._cache.epoch(note.workspace, owner_id), limit)
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        filepath = note_filepath(ws_path, note.folder, note.title)
        relative = str(Path(filepath).relative_to(ws_path))
        entries = GitRepository(ws_path).file_history(relative, limit=limit)
        if key is not None:
            self._cache.put(key, entries)
        return entries
```

Add epoch bumps after each successful write (one line each, just before the existing `logger.info` calls):

- `save()` — after the `self._repo.insert(...)` call (line 42):

```python
        if self._cache is not None:
            self._cache.bump(ws_name, user_id)
```

- `update()` — after the `self._repo.update(...)` call (line 131):

```python
        if self._cache is not None:
            self._cache.bump(note.workspace, owner_id)
```

- `delete()` — after the `self._repo.delete(...)` call (line 143):

```python
        if self._cache is not None:
            self._cache.bump(note.workspace, owner_id)
```

- `reindex()` — after the `for` loop, before `logger.info` (line 196):

```python
        if self._cache is not None:
            self._cache.bump(ws_name, owner_id)
```

(`restore_version` delegates to `update` — covered.)

- [ ] **Step 6: Wire up in `dependencies.py`**

In `src/kajet_turbo/dependencies.py`, replace line 21 (`note_service = NoteService(note_repo)`) with:

```python
from kajet_turbo.cache import WorkspaceCache, cache_enabled

note_service = NoteService(note_repo, cache=WorkspaceCache() if cache_enabled() else None)
```

(imports go to the top import block).

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_cache.py tests/test_services.py tests/test_concurrency_stress.py -v`
Expected: all pass. (`test_services.py` constructs `NoteService(note_repo)` — the new arg defaults to `None`, so nothing breaks; the stress test exercises the cache via `dependencies` only if it builds the service that way — it builds with no cache, fine.)

- [ ] **Step 8: Update the stress test to also cover the cache**

In `tests/test_concurrency_stress.py`, change the fixture line:

```python
    service = NoteService(NoteRepository(db.engine))
```

to:

```python
    from kajet_turbo.cache import WorkspaceCache
    service = NoteService(NoteRepository(db.engine), cache=WorkspaceCache())
```

Run: `uv run pytest tests/test_concurrency_stress.py tests/ -q`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/kajet_turbo/cache.py tests/test_cache.py src/kajet_turbo/services/notes.py src/kajet_turbo/dependencies.py tests/test_concurrency_stress.py pyproject.toml uv.lock
git commit -m "feat: add epoch-invalidated TTL cache for search and history"
```

---

### Task 9: Benchmark refactored code on Python 3.14

**Files:**
- Create: `docs/benchmarks/refactor-py314.json`
- Create: `docs/benchmarks/refactor-py314-nocache.json`

- [ ] **Step 1: Run with cache (the default)**

Run: `uv run python scripts/bench.py --label refactor-py314 --out docs/benchmarks/refactor-py314.json`
Expected: errors 0.

- [ ] **Step 2: Run with cache disabled (isolates the cache's contribution)**

Run: `KAJET_CACHE=0 uv run python scripts/bench.py --label refactor-py314-nocache --out docs/benchmarks/refactor-py314-nocache.json`
Expected: errors 0.

- [ ] **Step 3: Quick comparison**

Run: `uv run python scripts/bench_report.py docs/benchmarks/baseline-py314.json docs/benchmarks/refactor-py314-nocache.json docs/benchmarks/refactor-py314.json`
Expected: `mixed_80_20@c20` and `note_html@c50` p95/p99 clearly better than baseline (event loop no longer blocked by writes). Record observations for the final report.

- [ ] **Step 4: Commit**

```bash
git add docs/benchmarks/refactor-py314.json docs/benchmarks/refactor-py314-nocache.json
git commit -m "bench: record refactored code on Python 3.14 (cache on/off)"
```

---

### Task 10: Switch runtime to free-threaded 3.14t

**Files:**
- Create: `.python-version`
- Modify: `pyproject.toml` (sqlalchemy floor)
- Modify: `Dockerfile:8,17-18`

- [ ] **Step 1: Pin sqlalchemy with free-threading fixes**

In `pyproject.toml` `[project].dependencies`, add:

```toml
    "sqlalchemy>=2.0.45",
```

(2.0.44/2.0.45 fixed free-threading races in pool setup and `FromClause.c` init; sqlmodel pulls sqlalchemy transitively, this just sets the floor.)

- [ ] **Step 2: Create `.python-version`**

```
3.14t
```

- [ ] **Step 3: Recreate the venv on 3.14t**

Run: `uv python install 3.14t && uv sync`
Expected: uv downloads cpython-3.14t and rebuilds `.venv`. If any package fails to install (no cp314t wheel and no sdist build), STOP and report — this is the spec's documented fallback point.

- [ ] **Step 4: Verify the GIL is actually off**

Run: `uv run python -c "import sys; print(sys.version); print('GIL enabled:', sys._is_gil_enabled())"`
Expected: version contains `free-threading` (or `t` suffix) and `GIL enabled: False`. If `True`, a dependency re-enabled it — run `uv run python -X gil=0 -c "..."` to find which module warns, and report.

- [ ] **Step 5: Run the full test suite on 3.14t**

Run: `uv run pytest`
Expected: all pass, including the stress test. Watch for new warnings about thread safety; investigate any failure with the systematic-debugging skill rather than pinning back to 3.14.

- [ ] **Step 6: Update the Dockerfile**

Replace line 8 (`FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim`) and the dependency layer (lines 17-18) so the image uses 3.14t:

```dockerfile
FROM ghcr.io/astral-sh/uv:bookworm-slim
```

and:

```dockerfile
COPY pyproject.toml uv.lock .python-version ./
RUN uv python install && uv sync --frozen --no-dev --no-install-project
```

(`uv python install` with no argument reads `.python-version` → installs cpython-3.14t.)

- [ ] **Step 7: Verify the image**

Run: `docker build -t kajet-turbo:314t . && docker run --rm --entrypoint uv kajet-turbo:314t run python -c "import sys; print(sys._is_gil_enabled())"`
Expected: image builds; prints `False`.

- [ ] **Step 8: Commit**

```bash
git add .python-version pyproject.toml uv.lock Dockerfile
git commit -m "feat: switch runtime to free-threaded Python 3.14t"
```

---

### Task 11: Final benchmark, report, cache verdict

**Files:**
- Create: `docs/benchmarks/refactor-py314t.json`
- Create: `docs/benchmarks/2026-06-11-report.md`

- [ ] **Step 1: Benchmark on 3.14t**

Run: `uv run python scripts/bench.py --label refactor-py314t --out docs/benchmarks/refactor-py314t.json`
Expected: errors 0, JSON has `"free_threading": true`.

- [ ] **Step 2: Generate the comparison report**

Run:

```bash
uv run python scripts/bench_report.py \
    docs/benchmarks/baseline-py314.json \
    docs/benchmarks/refactor-py314-nocache.json \
    docs/benchmarks/refactor-py314.json \
    docs/benchmarks/refactor-py314t.json \
    > docs/benchmarks/2026-06-11-report.md
```

Then append (manually, below the table) a short "Wnioski" section answering:
1. How much did unblocking the event loop improve `mixed_80_20@c20` / `note_html@c50` p95-p99? (baseline vs refactor-nocache)
2. What did the cache add? (refactor-nocache vs refactor) — **verdict per the spec's ≥20 % rule**.
3. What did 3.14t change? (refactor py314 vs py314t) — look especially at `search_inproc@t4/t8` scaling and single-request `@c1` latencies (the interpreter tax).
4. `server_rss_mb` across runs.

- [ ] **Step 3: Apply the cache verdict**

If the cache fails the ≥20 % rule on both `search` and `history` scenarios: revert Task 8 entirely (changes to `services/notes.py` and `dependencies.py`, plus delete `cache.py` and `tests/test_cache.py`, drop cachetools from `pyproject.toml`) and state the measured numbers in the commit message. If it passes for only one of the two, keep the integration only for that one. If both pass, keep both.

- [ ] **Step 4: Commit**

```bash
git add docs/benchmarks/refactor-py314t.json docs/benchmarks/2026-06-11-report.md
git commit -m "bench: final 3.14t measurements and comparison report"
```

- [ ] **Step 5: Verify everything one last time**

Run: `uv run pytest && uv run ruff check src/ tests/ scripts/`
Expected: tests pass, no lint errors. Fix any lint findings and amend.

- [ ] **Step 6: Finish**

Use the superpowers:finishing-a-development-branch skill (or report results to the user if working on main, as this plan does — commits are already on main per repo convention).
