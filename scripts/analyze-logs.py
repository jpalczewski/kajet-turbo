"""Analyze kajet-turbo JSONL logs from ops/logs/.

Usage:
    uv run python scripts/analyze-logs.py                        # Loki: 24h, produkcja, mcp
    uv run python scripts/analyze-logs.py ops/logs/foo.log       # file → docker-logs
    uv run python scripts/analyze-logs.py --source docker-logs   # docker-logs: latest in ops/logs/
    uv run python scripts/analyze-logs.py --since 6h --env develop
    uv run python scripts/analyze-logs.py --mode sessions        # session timeline
    uv run python scripts/analyze-logs.py --mode workspaces      # workspace switches + scope
    uv run python scripts/analyze-logs.py --mode errors          # warnings and above
    uv run python scripts/analyze-logs.py --mode tools           # tool call summary
    uv run python scripts/analyze-logs.py --mode events          # published events pipeline
    uv run python scripts/analyze-logs.py --mode http            # HTTP requests
    uv run python scripts/analyze-logs.py --mode percentiles --pct-field db_ms --group-by tool
    # p50/p95/p99; --pct-field also takes duration_ms/fts_ms/vec_ms/meta_ms
    uv run python scripts/analyze-logs.py --grep save_note       # filter by substring
    uv run python scripts/analyze-logs.py --grep "note_upd|ws_c" --re  # regex grep
    uv run python scripts/analyze-logs.py --msg save_note        # filter by msg field
    uv run python scripts/analyze-logs.py --msg save_note,note_updated  # multiple msg values
    uv run python scripts/analyze-logs.py --fields ts,msg,note_id,session_id  # custom columns
    uv run python scripts/analyze-logs.py --msg save_note --fields ts,user_id,duration_ms
    uv run python scripts/analyze-logs.py --role mcp             # pick latest mcp log
    uv run python scripts/analyze-logs.py --env develop --role api  # develop environment
    uv run python scripts/analyze-logs.py --since 7d --pct-field fts_ms \
        --compare-at 2026-08-22T19:59:08Z   # did that deploy/fix change anything?
    uv run python scripts/analyze-logs.py --json | jq 'select(.msg=="http")'  # raw JSONL

Reusable pieces live in scripts/log_analysis.py — import those for one-off analysis
instead of re-deriving them here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict

from log_analysis import (
    LEVEL_ORDER,
    load_events,
    normalize_env,
    percentile_summary,
    split_at,
    summarize,
)


def _fmt_field(e: dict, key: str) -> str:
    v = e.get(key)
    if v is None:
        return ""
    if isinstance(v, str) and len(v) > 40:
        return v[:40]
    return str(v)


# ── modes ──────────────────────────────────────────────────────────────────────


def mode_sessions(events: list[dict]) -> None:
    sessions: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        s = e.get("session_id")
        if s:
            sessions[s].append(e)

    print(f"{'Session ID':36}  {'#events':>7}  {'First':19}  {'Last':19}")
    print("-" * 90)
    for sid, evs in sorted(sessions.items(), key=lambda kv: kv[1][0].get("ts", "")):
        first = evs[0].get("ts", "")[:19]
        last = evs[-1].get("ts", "")[:19]
        print(f"{sid:36}  {len(evs):>7}  {first}  {last}")

    null_count = sum(1 for e in events if e.get("session_id") is None)
    print(f"\n  (+ {null_count} events with session_id=null)")


def mode_workspaces(events: list[dict]) -> None:
    relevant_msgs = {
        "workspace_switched",
        "activate_workspace",
        "active_workspace_resolved",
        "active_workspace_miss",
        "db_fallback",
    }
    for e in events:
        msg = e.get("msg", "")
        if msg not in relevant_msgs and "workspace" not in msg.lower():
            continue
        ts = e.get("ts", "")[:19]
        lvl = e.get("level", "")[:4].upper()
        ws = e.get("ws") or e.get("workspace") or ""
        scope = e.get("scope") or ""
        source = e.get("source") or ""
        sess = (e.get("session_id") or "")[:12]
        extras = " ".join(
            filter(
                None,
                [
                    f"ws={ws}" if ws else "",
                    f"scope={scope}" if scope else "",
                    f"source={source}" if source else "",
                    f"sess={sess}…" if sess else "",
                ],
            )
        )
        print(f"{ts}  [{lvl:4}]  {msg:<40}  {extras}")


def mode_errors(events: list[dict], min_level: str = "warning") -> None:
    threshold = LEVEL_ORDER.get(min_level, 2)
    found = False
    for e in events:
        if LEVEL_ORDER.get(e.get("level", "debug"), 0) >= threshold:
            ts = e.get("ts", "")[:19]
            lvl = e.get("level", "").upper()
            msg = e.get("msg", "")
            exc = e.get("exc_info", "")
            print(f"{ts}  [{lvl:8}]  {msg}")
            if exc:
                print(f"  {exc[:200]}")
            found = True
    if not found:
        print(f"No events at level >= {min_level}")


def mode_tools(events: list[dict]) -> None:
    tool_events = [e for e in events if e.get("tool")]
    if not tool_events:
        print("No tool call events found (look for events with 'tool' field).")
        return

    counts: Counter = Counter()
    durations: dict[str, list[float]] = defaultdict(list)
    errors: Counter = Counter()

    for e in tool_events:
        tool = e["tool"]
        counts[tool] += 1
        if "duration_ms" in e:
            durations[tool].append(e["duration_ms"])
        if e.get("level") in ("error", "warning"):
            errors[tool] += 1

    print(f"{'Tool':<30}  {'calls':>6}  {'avg_ms':>7}  {'max_ms':>7}  {'errors':>6}")
    print("-" * 65)
    for tool, count in counts.most_common():
        durs = durations[tool]
        avg = f"{sum(durs) / len(durs):.0f}" if durs else "-"
        mx = f"{max(durs):.0f}" if durs else "-"
        err = errors[tool] or ""
        print(f"{tool:<30}  {count:>6}  {avg:>7}  {mx:>7}  {err!s:>6}")


def mode_events(events: list[dict]) -> None:
    """Show published note_updated / workspace_changed events and tool calls that produce them."""
    relevant = {"note_updated", "workspace_changed"}
    tool_completion_msgs = {e.get("tool") for e in events if e.get("tool")}

    found = False
    for e in events:
        msg = e.get("msg", "")
        if msg not in relevant and msg not in tool_completion_msgs:
            continue
        if msg in tool_completion_msgs and e.get("level") not in ("info", "warning", "error"):
            continue
        ts = e.get("ts", "")[:19]
        lvl = (e.get("level") or "info")[:4].upper()
        uid = (e.get("user_id") or "")[:16]
        note_id = e.get("note_id") or ""
        workspace = e.get("workspace") or e.get("ws") or ""
        sess = (e.get("session_id") or "null")[:8]
        extras = "  ".join(
            filter(
                None,
                [
                    f"note={note_id}" if note_id else "",
                    f"ws={workspace}" if workspace else "",
                    f"user={uid}" if uid else "",
                    f"sess={sess}",
                ],
            )
        )
        print(f"{ts}  [{lvl}]  {msg:<35}  {extras}")
        found = True

    if not found:
        print("No event pipeline entries found.")


def mode_http(events: list[dict]) -> None:
    """Show HTTP requests: method, path, status, duration."""
    found = False
    for e in events:
        if e.get("msg") != "http":
            continue
        ts = e.get("ts", "")[:19]
        method = (e.get("method") or "")[:6]
        path = (e.get("path") or "")[:60]
        status = e.get("status") or ""
        dur = e.get("duration_ms")
        dur_str = f"{dur}ms" if dur is not None else ""
        uid = (e.get("user_id") or "")[:30]
        print(f"{ts}  {method:<6}  {status!s:>3}  {dur_str:>7}  {path:<60}  {uid}")
        found = True
    if not found:
        print("No HTTP request events found.")


def mode_grep(events: list[dict], pattern: str, use_re: bool = False) -> None:
    compiled = re.compile(pattern, re.IGNORECASE) if use_re else None
    for e in events:
        raw = json.dumps(e)
        if compiled:
            if not compiled.search(raw):
                continue
        elif pattern.lower() not in raw.lower():
            continue
        ts = e.get("ts", "")[:19]
        lvl = (e.get("level") or "")[:4].upper()
        msg = e.get("msg", "")
        rest = {k: v for k, v in e.items() if k not in ("ts", "level", "msg")}
        rest_str = "  ".join(f"{k}={v}" for k, v in rest.items() if v is not None and v != "")
        print(f"{ts}  [{lvl}]  {msg}  {rest_str}")


def mode_msg(events: list[dict], msg_filter: set[str], fields: list[str] | None) -> None:
    """Filter by msg field(s), optionally printing only specified fields as columns."""
    matched = [e for e in events if e.get("msg") in msg_filter]
    if not matched:
        print(f"No events with msg in {sorted(msg_filter)}")
        return

    if fields:
        header = "  ".join(f"{f:<20}" for f in fields)
        print(header)
        print("-" * len(header))
        for e in matched:
            cols = []
            for f in fields:
                v = e.get(f)
                if v is None:
                    cols.append(" " * 20)
                else:
                    s = str(v)
                    if f in ("ts",):
                        s = s[:19]
                    elif f == "session_id" and len(s) > 8:
                        s = s[:8]
                    cols.append(f"{s:<20}")
            print("  ".join(cols))
    else:
        mode_grep(matched, "", use_re=False)


def mode_percentiles(events: list[dict], field: str, group_by: str) -> None:
    summary = percentile_summary(events, field, group_by)
    if not summary:
        print(f"No events with numeric {field!r} grouped by {group_by!r}.")
        return
    print(f"{group_by:<30}  {'count':>6}  {'p50':>7}  {'p95':>7}  {'p99':>7}  {'max':>7}")
    print("-" * 74)

    def _f(x: float | None) -> str:
        return f"{x:.0f}" if x is not None else "-"

    for g, s in sorted(summary.items(), key=lambda kv: kv[1]["p95"] or 0, reverse=True):
        print(
            f"{g:<30}  {s['count']:>6}  {_f(s['p50']):>7}  {_f(s['p95']):>7}  "
            f"{_f(s['p99']):>7}  {_f(s['max']):>7}"
        )


def mode_compare(events: list[dict], boundary: str, field: str) -> None:
    """Same field, before and after a moment — the "did that change anything?" view."""
    before, after = split_at(events, boundary)
    b = summarize([float(e[field]) for e in before if isinstance(e.get(field), (int, float))])
    a = summarize([float(e[field]) for e in after if isinstance(e.get(field), (int, float))])

    def _f(x: float | None) -> str:
        return f"{x:.1f}" if x is not None else "-"

    def _delta(x: float | None, y: float | None) -> str:
        if x is None or y is None:
            return "-"
        if x == 0:
            return "+inf" if y else "0"
        return f"{y / x:.1f}x"

    print(f"{field} split at {boundary}\n")
    print(f"{'':>8}  {'before':>10}  {'after':>10}  {'change':>8}")
    print("-" * 42)
    for k in ("count", "p50", "p95", "p99", "max"):
        change = _delta(b[k], a[k]) if k != "count" else ""
        print(f"{k:>8}  {_f(b[k]):>10}  {_f(a[k]):>10}  {change:>8}")


def mode_fields(events: list[dict], fields: list[str]) -> None:
    """Print all events with only the specified fields as columns."""
    header = "  ".join(f"{f:<20}" for f in fields)
    print(header)
    print("-" * len(header))
    for e in events:
        cols = []
        for f in fields:
            v = e.get(f)
            if v is None:
                cols.append(" " * 20)
            else:
                s = str(v)
                if f == "ts":
                    s = s[:19]
                elif f == "session_id" and len(s) > 8:
                    s = s[:8]
                cols.append(f"{s:<20}")
        print("  ".join(cols))


# ── main ───────────────────────────────────────────────────────────────────────

MODES = ("sessions", "workspaces", "errors", "tools", "events", "http", "percentiles", "all")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze kajet-turbo ops logs")
    parser.add_argument("log", nargs="?", help="Log file path (default: latest produkcja_mcp*)")
    parser.add_argument(
        "--role",
        default="mcp",
        help="Role filter for auto-detection: mcp, api, worker (default: mcp)",
    )
    parser.add_argument(
        "--env",
        default="produkcja",
        help="Environment: produkcja (default) | develop",
    )
    parser.add_argument("--mode", choices=MODES, default="all", help="Analysis mode")
    parser.add_argument("--grep", metavar="PATTERN", help="Filter events containing PATTERN")
    parser.add_argument("--re", dest="use_re", action="store_true", help="Treat --grep as regex")
    parser.add_argument(
        "--msg",
        metavar="MSG[,MSG]",
        help="Filter by msg field (exact, comma-separated for multiple)",
    )
    parser.add_argument(
        "--fields",
        metavar="FIELD[,FIELD]",
        help="Print only these fields as columns (comma-separated); use with --msg or alone",
    )
    parser.add_argument("--min-level", default="warning", help="Minimum log level for errors mode")
    parser.add_argument(
        "--pct-field",
        default="duration_ms",
        help="Numeric field for percentiles mode (default: duration_ms; also db_ms/fts_ms/vec_ms)",
    )
    parser.add_argument(
        "--group-by",
        default="tool",
        help="Grouping field for percentiles mode (default: tool; e.g. op, path)",
    )
    parser.add_argument(
        "--source",
        choices=("loki", "docker-logs"),
        default=None,
        help="Event source (default: loki, unless a log file path is given -> docker-logs)",
    )
    parser.add_argument(
        "--since", default="24h", help="Loki query start (e.g. 1h, 24h, 7d, ISO timestamp)"
    )
    parser.add_argument("--until", default="now", help="Loki query end (default: now)")
    parser.add_argument(
        "--compare-at",
        metavar="ISO_TS",
        help="Split the window at this timestamp and compare --pct-field on both sides "
        "(e.g. a deploy or a fix: did it change anything?)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw events as JSONL instead of a report — for jq or ad-hoc analysis",
    )
    args = parser.parse_args(argv)

    if args.source is None:
        args.source = "docker-logs" if args.log else "loki"

    if args.log and args.source == "loki":
        parser.error(
            "a log file path was given but --source loki was also specified; drop one of the two"
        )

    return args


def main() -> None:
    args = parse_args()

    env = normalize_env(args.env)

    if args.source == "loki":
        banner = f"→ loki: role={args.role} env={env} since={args.since} until={args.until}"
    else:
        banner = f"→ {args.log or 'latest ' + env + '_' + args.role}"
    if not args.json:
        print(banner + "\n")

    try:
        events = load_events(
            source=args.source,
            role=args.role,
            env=env,
            since=args.since,
            until=args.until,
            log=args.log,
            min_level=args.min_level if args.mode == "errors" else None,
            # A compare run needs both sides of the boundary, so it must not be narrowed
            # to one msg at query time.
            msg_filter=[m.strip() for m in args.msg.split(",")]
            if (args.msg and not args.compare_at)
            else None,
        )
    except Exception as e:  # LokiUnreachableError carries the remediation text
        if type(e).__name__ != "LokiUnreachableError":
            raise
        sys.exit(str(e))

    if args.json:
        for e in events:
            print(json.dumps(e, ensure_ascii=False))
        return

    print(f"  {len(events)} events parsed\n")

    if args.compare_at:
        mode_compare(events, args.compare_at, args.pct_field)
        return

    fields = [f.strip() for f in args.fields.split(",")] if args.fields else None
    msg_filter = {m.strip() for m in args.msg.split(",")} if args.msg else None

    # --msg (with optional --fields)
    if msg_filter:
        mode_msg(events, msg_filter, fields)
        return

    # --fields alone (no --msg): print all events with those columns
    if fields:
        mode_fields(events, fields)
        return

    # --grep
    if args.grep:
        mode_grep(events, args.grep, use_re=args.use_re)
        return

    # named modes
    if args.mode == "sessions" or args.mode == "all":
        print("═══ SESSIONS ═══")
        mode_sessions(events)
        print()

    if args.mode == "workspaces" or args.mode == "all":
        print("═══ WORKSPACES ═══")
        mode_workspaces(events)
        print()

    if args.mode == "tools" or args.mode == "all":
        print("═══ TOOLS ═══")
        mode_tools(events)
        print()

    if args.mode == "events" or args.mode == "all":
        print("═══ EVENTS PIPELINE ═══")
        mode_events(events)
        print()

    if args.mode == "http":
        mode_http(events)

    if args.mode == "percentiles":
        mode_percentiles(events, args.pct_field, args.group_by)

    if args.mode == "errors" or args.mode == "all":
        print("═══ ERRORS / WARNINGS ═══")
        mode_errors(events, args.min_level)


if __name__ == "__main__":
    main()
