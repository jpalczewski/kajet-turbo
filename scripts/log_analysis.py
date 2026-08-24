"""Reusable pieces of kajet-turbo log analysis.

``analyze-logs.py`` is a CLI and its filename has a hyphen, so nothing can import it —
its own test has to load it through ``importlib.util.spec_from_file_location``. Everything
here is the part worth reusing: loading events, summarizing them, and stitching the
several log lines of one operation back together. One-off analysis imports this instead of
re-deriving it:

    import sys; sys.path.insert(0, "scripts")
    from log_analysis import load_events, operations, percentile_summary
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

LOGS_DIR = Path(__file__).parent.parent / "ops" / "logs"
LEVEL_ORDER = {"debug": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}


def normalize_env(env: str) -> str:
    if env in ("prod", "production"):
        return "produkcja"
    if env in ("dev", "development"):
        return "develop"
    return env


def parse_log(path: Path) -> list[dict]:
    """Parse a docker-logs capture: JSONL, each line prefixed with a docker timestamp."""
    events = []
    for line in path.read_text(errors="replace").splitlines():
        raw = line.strip()
        if not raw:
            continue
        idx = raw.find("{")
        if idx == -1:
            continue
        try:
            events.append(json.loads(raw[idx:]))
        except json.JSONDecodeError:
            continue
    return events


def latest_log(role: str = "mcp", env: str = "produkcja") -> Path:
    pattern = f"{env}_{role}*"
    candidates = sorted(LOGS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        sys.exit(f"No logs found in {LOGS_DIR} matching {pattern!r}")
    return candidates[0]


def load_events(
    *,
    source: str = "loki",
    role: str = "mcp",
    env: str = "produkcja",
    since: str = "24h",
    until: str = "now",
    log: str | Path | None = None,
    min_level: str | None = None,
    msg_filter: list[str] | None = None,
) -> list[dict]:
    """Events from either source, with the same shape out of both."""
    env = normalize_env(env)
    if source == "docker-logs":
        return parse_log(Path(log) if log else latest_log(role, env))
    # lazy: keeps the SSH/socket/subprocess surface out of pure docker-logs runs
    import loki_source

    return loki_source.fetch_events(
        role, env, since=since, until=until, min_level=min_level, msg_filter=msg_filter
    )


def event_time(event: dict) -> float | None:
    """Unix seconds from an event's ``ts``, or None when it is missing or unparseable."""
    ts = event.get("ts")
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def split_at(events: list[dict], boundary: str) -> tuple[list[dict], list[dict]]:
    """Partition events into (before, after) an ISO timestamp — for before/after questions
    like "did that deploy change anything?". Events without a usable ``ts`` are dropped."""
    edge = datetime.fromisoformat(boundary.replace("Z", "+00:00")).timestamp()
    before, after = [], []
    for e in events:
        t = event_time(e)
        if t is None:
            continue
        (before if t < edge else after).append(e)
    return before, after


def operations(
    events: list[dict], msgs: list[str], *, window_s: float = 2.0
) -> list[dict[str, Any]]:
    """Stitch the several log lines of one logical operation into one dict.

    A search records its query shape on ``search_performed`` and its timings on
    ``search_notes``, and those two lines do **not** share a ``request_id``:
    ``LoggingMiddleware`` binds the HTTP one and ``logged_tool`` rebinds the MCP one from
    the tool context. So grouping has to fall back on time.

    Lines are walked in timestamp order; a line joins the open operation when it is within
    ``window_s`` of that operation's first line and its ``msg`` is not already present.
    Concurrent operations of the same kind inside one window would interleave — fine at
    this traffic level, wrong under load, so treat the result as a sample not a ledger.

    Each returned dict carries the merged fields plus ``_msgs`` (what was merged) and
    ``_ts`` (the first line's timestamp).
    """
    wanted = set(msgs)
    timed = [(t, e) for e in events if e.get("msg") in wanted and (t := event_time(e)) is not None]
    timed.sort(key=lambda pair: pair[0])

    out: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    start = 0.0
    for t, e in timed:
        if current is None or t - start > window_s or e["msg"] in current["_msgs"]:
            current = {"_msgs": [], "_ts": e.get("ts")}
            start = t
            out.append(current)
        current["_msgs"].append(e["msg"])
        current.update({k: v for k, v in e.items() if k != "msg"})
    return out


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    """Nearest-rank percentile (no interpolation, dependency-free, deterministic)."""
    if not sorted_vals:
        return None
    k = math.ceil(p / 100 * len(sorted_vals)) - 1
    return sorted_vals[max(0, min(k, len(sorted_vals) - 1))]


def numeric_values(events: list[dict], field: str) -> list[float]:
    """Every numeric value of ``field``, sorted. Booleans are not numbers here."""
    vals = [
        float(v)
        for e in events
        if isinstance(v := e.get(field), (int, float)) and not isinstance(v, bool)
    ]
    vals.sort()
    return vals


def summarize(values: list[float]) -> dict[str, float | int | None]:
    vals = sorted(values)
    if not vals:
        return {"count": 0, "p50": None, "p95": None, "p99": None, "max": None}
    return {
        "count": len(vals),
        "p50": _percentile(vals, 50),
        "p95": _percentile(vals, 95),
        "p99": _percentile(vals, 99),
        "max": vals[-1],
    }


def percentile_summary(
    events: list[dict], field: str, group_by: str
) -> dict[str, dict[str, float | int | None]]:
    """Group events by ``group_by`` and summarize numeric ``field`` per group.

    Only completion lines carry these fields; slow_sync is threshold-gated and
    survivorship-biased, so it is intentionally NOT the source here."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for e in events:
        g = e.get(group_by)
        v = e.get(field)
        if g is None or not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        buckets[str(g)].append(float(v))
    return {g: summarize(vals) for g, vals in buckets.items()}
