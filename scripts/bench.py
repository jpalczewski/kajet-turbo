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
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ADMIN_EMAIL = "bench@local"
ADMIN_PASSWORD = "bench-password"
WS = "bench"
PORT = 8765
WORDS = [
    "projekt",
    "klient",
    "spotkanie",
    "notatka",
    "pomysl",
    "budzet",
    "raport",
    "analiza",
    "wdrozenie",
    "testy",
    "architektura",
    "baza",
    "wyszukiwanie",
    "frontend",
    "backend",
    "python",
    "sqlite",
    "asyncio",
    "watki",
    "cache",
]
QUERIES = [
    "projekt klient",
    "raport budzet",
    "sqlite cache",
    "testy backend",
    "architektura baza",
    "python asyncio",
    "notatka spotkanie",
    "frontend wdrozenie",
    "analiza raport",
    "watki python",
]


def make_content(rng: random.Random, paragraphs: int = 6) -> str:
    return "\n\n".join(" ".join(rng.choices(WORDS, k=60)) for _ in range(paragraphs))


def percentiles(latencies_ms: list[float]) -> dict | None:
    if not latencies_ms:
        return None
    xs = sorted(latencies_ms)

    def pct(p: float) -> float:
        return round(xs[min(len(xs) - 1, max(0, round(p / 100 * (len(xs) - 1))))], 2)

    return {
        "p50": pct(50),
        "p95": pct(95),
        "p99": pct(99),
        "mean": round(statistics.fmean(xs), 2),
        "count": len(xs),
    }


def rss_mb(pid: int) -> float:
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout.strip()
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
        "MCP_BASE_URL": f"http://127.0.0.1:{PORT}",
        "LOG_LEVEL": "WARNING",
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", "from kajet_turbo.server import main; main()"], env=env
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited during startup, code {proc.returncode}")
        try:
            httpx.get(f"http://127.0.0.1:{PORT}/api/session", timeout=1)
            return proc
        except httpx.HTTPError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("server did not become ready in 30s")


async def login(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    r.raise_for_status()


async def seed(
    client: httpx.AsyncClient, n_notes: int, rng: random.Random
) -> tuple[list[str], dict]:
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
                json={
                    "title": f"Nota {i}",
                    "content": make_content(rng),
                    "tags": [rng.choice(WORDS)],
                    "folder": "",
                },
            )
            latencies.append((time.perf_counter() - t0) * 1000)
            r.raise_for_status()
            note_ids.append(r.json()["note_id"])

    t0 = time.perf_counter()
    await asyncio.gather(*(create(i) for i in range(n_notes)))
    wall = time.perf_counter() - t0
    return note_ids, {
        "latency_ms": percentiles(latencies),
        "rps": round(n_notes / wall, 1),
        "errors": 0,
    }


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
                else:
                    latencies.append((time.perf_counter() - t0) * 1000)
            except httpx.HTTPError:
                errors += 1

    t0 = time.perf_counter()
    await asyncio.gather(*(one(i) for i in range(total)))
    wall = time.perf_counter() - t0
    lat = percentiles(latencies)
    return {"latency_ms": lat, "rps": round(len(latencies) / wall, 1), "errors": errors}


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
                json={"content": make_content(rng)},
            )

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
    """Threaded search against the seeded DB, server stopped. Measures FTS5
    C-code scaling across real threads (the free-threading payoff)."""
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor

    # No kajet_turbo import may happen before these env vars are set (import-order dependency).
    os.environ["LOG_LEVEL"] = "WARNING"
    os.environ["DB_PATH"] = str(tmp / "bench.db")
    os.environ["WORKSPACES_DIR"] = str(tmp / "workspaces")
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.notes import NoteRepository
    from kajet_turbo.services.notes import NoteService

    owner_id = (
        sqlite3.connect(str(tmp / "bench.db"))
        .execute('SELECT id FROM "users" LIMIT 1')
        .fetchone()[0]
    )
    db = Database()
    svc = NoteService(NoteRepository(db.engine))
    results: dict[str, dict] = {}
    total = 200
    for threads in (1, 4, 8):
        latencies: list[float] = []

        def one(i: int, lat: list[float] = latencies) -> None:
            t0 = time.perf_counter()
            svc.search(QUERIES[i % len(QUERIES)], [WS], owner_id=owner_id, limit=10)
            lat.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=threads) as ex:
            list(ex.map(one, range(total)))
        wall = time.perf_counter() - t0
        results[f"search_inproc@t{threads}"] = {
            "latency_ms": percentiles(latencies),
            "rps": round(total / wall, 1),
            "errors": 0,
        }

    from kajet_turbo.workspace import workspace_path

    t0 = time.perf_counter()
    reindexed = svc.reindex(WS, owner_id=owner_id, ws_path=workspace_path(WS, user_id=owner_id))
    wall = time.perf_counter() - t0
    results["reindex_total"] = {
        "latency_ms": percentiles([wall * 1000]),
        "rps": round(reindexed["count"] / wall, 1),
        "errors": 0,
    }
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
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
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
    total_errors = sum(s.get("errors", 0) for s in scenarios.values())
    if total_errors > 0:
        print(
            f"\nWARNING: {total_errors} request error(s) detected across all "
            "scenarios — results may be unreliable!",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
