"""Write-path benchmark against workspace size (issue #343).

Spawns the server on a temp DB + temp workspaces dir, grows one workspace to each
target size through the batch endpoint, and at every size measures single-note creates
sequentially and under concurrency. Client latency comes from httpx; the server-side
split (git_ms, git_lock_wait_ms, db_ms, ...) comes from the ``http`` perf-span log lines
the server writes to stderr. Emits a bench_report.py-compatible JSON and prints a
markdown table, so a before/after pair diffs with the existing report tool:

    uv run python scripts/bench_git_write.py --label before --out /tmp/before.json
    uv run python scripts/bench_git_write.py --label after --out /tmp/after.json
    uv run python scripts/bench_report.py /tmp/before.json /tmp/after.json

``--keep`` leaves the seeded temp dir behind and prints the env needed to start the
server against it by hand — a ready-made large workspace to develop against.
"""

import argparse
import asyncio
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from bench import percentiles

ADMIN_EMAIL = "bench@local"
ADMIN_PASSWORD = "bench-password"
WS = "bench"
PORT = 8766
BASE_URL = f"http://127.0.0.1:{PORT}"
NOTE_ROUTE = "/api/workspaces/{name}/notes"
BATCH_LIMIT = 50  # BatchCreateNotesRequest.notes max_length
FOLDER_SIZE = 100  # seeded notes per folder, so the tree has some depth
SPAN_FIELDS = ("duration_ms", "workspace_write_ms", "git_ms", "git_lock_wait_ms", "db_ms")
CONTENT = "# Note\n\n" + ("lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 40) + "\n"


# --- server ------------------------------------------------------------------


def server_env(tmp: Path) -> dict[str, str]:
    return {
        "DB_PATH": str(tmp / "bench.db"),
        "WORKSPACES_DIR": str(tmp / "workspaces"),
        "KAJET_ADMIN_EMAIL": ADMIN_EMAIL,
        "KAJET_ADMIN_PASSWORD": ADMIN_PASSWORD,
        "MCP_PORT": str(PORT),
        "MCP_HOST": "127.0.0.1",
        "MCP_BASE_URL": BASE_URL,
        "KAJET_SERVE_SPA": "0",
        # The perf fields ride on INFO-level "http" lines.
        "LOG_LEVEL": "INFO",
        # Seeding enqueues one reindex job per note; a sleeping worker keeps those from
        # competing with the measured writes for the SQLite writer.
        "KAJET_WORKER_POLL_INTERVAL": "3600",
    }


@dataclass
class Server:
    proc: subprocess.Popen
    log_path: Path

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def spawn_server(tmp: Path) -> Server:
    log_path = tmp / "server.log"
    proc = subprocess.Popen(
        [sys.executable, "-c", "from kajet_turbo.server import main; main()"],
        env={**os.environ, **server_env(tmp)},
        stderr=log_path.open("wb"),
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited during startup, code {proc.returncode}")
        try:
            if httpx.get(f"{BASE_URL}/readyz", timeout=1).status_code == 200:
                return Server(proc, log_path)
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    proc.kill()
    raise RuntimeError("server did not become ready in 30s")


# --- server-side spans (pure, tested) ----------------------------------------


def parse_note_create_spans(lines: Iterable[str]) -> list[dict]:
    """Keep the ``http`` log records for successful single-note creates."""
    spans: list[dict] = []
    for line in lines:
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            record.get("msg") == "http"
            and record.get("method") == "POST"
            and record.get("path") == NOTE_ROUTE
            and record.get("status") == 201
        ):
            spans.append(record)
    return spans


def summarize_spans(spans: list[dict]) -> dict[str, float]:
    """Median of every perf field present, in ms. Missing fields count as 0 — a span
    that never touched git legitimately has no git_ms."""
    if not spans:
        return {}
    return {
        f: round(statistics.median(s.get(f, 0) for s in spans), 1)
        for f in SPAN_FIELDS
        if any(f in s for s in spans)
    }


@dataclass
class LogTail:
    """Reads only the lines appended since the last call, so each phase sees its own
    spans and not the seed traffic before it."""

    path: Path
    offset: int = 0

    def new_spans(self) -> list[dict]:
        with self.path.open("rb") as fh:
            fh.seek(self.offset)
            data = fh.read()
            self.offset = fh.tell()
        return parse_note_create_spans(data.decode("utf-8", errors="replace").splitlines())

    def discard(self) -> None:
        self.offset = self.path.stat().st_size


# --- client ------------------------------------------------------------------


async def login(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    r.raise_for_status()
    r = await client.post("/api/workspaces", json={"name": WS})
    r.raise_for_status()


async def seed_to(client: httpx.AsyncClient, seeded: int, target: int) -> int:
    """Grow the workspace to ``target`` notes with batch creates (one commit per batch)."""
    while seeded < target:
        n = min(BATCH_LIMIT, target - seeded)
        notes = [
            {
                "title": f"seed {seeded + i}",
                "content": CONTENT,
                "folder": f"f{(seeded + i) // FOLDER_SIZE}",
            }
            for i in range(n)
        ]
        r = await client.post(f"/api/workspaces/{WS}/notes/batch", json={"notes": notes})
        r.raise_for_status()
        errors = [x for x in r.json()["results"] if x.get("error")]
        if errors:
            raise RuntimeError(f"seed batch rejected {len(errors)} notes: {errors[0]}")
        seeded += n
    return seeded


async def create_notes(
    client: httpx.AsyncClient, folder: str, tag: str, total: int, concurrency: int
) -> dict:
    latencies: list[float] = []
    errors = 0
    sem = asyncio.Semaphore(concurrency)

    async def one(i: int) -> None:
        nonlocal errors
        async with sem:
            t0 = time.perf_counter()
            try:
                r = await client.post(
                    f"/api/workspaces/{WS}/notes",
                    json={"title": f"{tag} {i}", "content": CONTENT, "folder": folder},
                )
                if r.status_code >= 400:
                    errors += 1
                else:
                    latencies.append((time.perf_counter() - t0) * 1000)
            except httpx.HTTPError:
                errors += 1

    t0 = time.perf_counter()
    await asyncio.gather(*(one(i) for i in range(total)))
    wall = time.perf_counter() - t0
    return {
        "latency_ms": percentiles(latencies),
        "rps": round(len(latencies) / wall, 1),
        "errors": errors,
    }


@dataclass
class Plan:
    sizes: list[int]
    writes: int
    concurrency: int
    warmup: int
    scenarios: dict[str, dict] = field(default_factory=dict)


async def run(plan: Plan, tail: LogTail) -> dict[str, dict]:
    seeded = 0
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=120) as client:
        await login(client)
        for size in plan.sizes:
            seeded = await seed_to(client, seeded, size)
            # A fresh folder per size: once commits cost ∝ touched-folder entries (#343),
            # a folder that keeps growing across phases would skew the comparison.
            folder = f"bench-{size}"
            # Untimed writes absorb the WAL checkpoint the seed batches leave behind.
            warm = await create_notes(client, folder, f"w{size}warm", plan.warmup, 1)
            seeded += plan.warmup - warm["errors"]
            tail.discard()
            for conc in (1, plan.concurrency):
                stats = await create_notes(client, folder, f"w{size}c{conc}", plan.writes, conc)
                stats["server_ms"] = summarize_spans(tail.new_spans())
                stats["files"] = seeded
                plan.scenarios[f"note_create@c{conc}/{size}files"] = stats
                seeded += plan.writes - stats["errors"]
            print(f"  {size} files done", file=sys.stderr)
    return plan.scenarios


# --- output ------------------------------------------------------------------


def render_table(result: dict) -> str:
    lines = [
        f"## {result['label']} — python {result['python']}, "
        f"free-threading {result['free_threading']}",
        "",
        "| scenario | files | client p50 | client p95 | rps | "
        + " | ".join(SPAN_FIELDS)
        + " | errors |",
        "|---|---|---|---|---|" + "---|" * len(SPAN_FIELDS) + "---|",
    ]
    for name, s in result["scenarios"].items():
        lat = s.get("latency_ms") or {}
        server = s.get("server_ms", {})
        cells = [
            name,
            str(s.get("files", "-")),
            str(lat.get("p50", "-")),
            str(lat.get("p95", "-")),
            str(s.get("rps", "-")),
            *(str(server.get(f, "-")) for f in SPAN_FIELDS),
            str(s.get("errors", 0)),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True, help="JSON result path (bench_report.py input)")
    ap.add_argument("--sizes", default="100,500,1000,2000", help="workspace sizes to grow through")
    ap.add_argument("--writes", type=int, default=20, help="single-note creates per phase")
    ap.add_argument("--concurrency", type=int, default=8, help="parallel clients for the c>1 phase")
    ap.add_argument("--warmup", type=int, default=5, help="untimed creates after each seed step")
    ap.add_argument(
        "--keep", action="store_true", help="keep the temp dir and print how to reuse it"
    )
    args = ap.parse_args()

    plan = Plan(
        sizes=sorted({int(x) for x in args.sizes.split(",")}),
        writes=args.writes,
        concurrency=args.concurrency,
        warmup=args.warmup,
    )
    tmp = Path(tempfile.mkdtemp(prefix="kajet-bench-git-"))
    server = spawn_server(tmp)
    try:
        scenarios = asyncio.run(run(plan, LogTail(server.log_path)))
    finally:
        server.stop()

    result = {
        "label": args.label,
        "python": sys.version.split()[0],
        "free_threading": bool(getattr(sys, "_is_gil_enabled", lambda: True)()) is False,
        "sizes": plan.sizes,
        "writes": plan.writes,
        "concurrency": plan.concurrency,
        "warmup": plan.warmup,
        "scenarios": scenarios,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(render_table(result))
    print(f"wrote {out}", file=sys.stderr)

    if args.keep:
        env = {**server_env(tmp), "KAJET_WORKER_POLL_INTERVAL": "1"}  # let reindex jobs drain
        line = " ".join(f"{k}={v}" for k, v in env.items())
        print(f"kept {tmp}\nrestart against it with:\n  {line} uv run kajet-turbo", file=sys.stderr)
    else:
        shutil.rmtree(tmp, ignore_errors=True)

    if sum(s["errors"] for s in scenarios.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
