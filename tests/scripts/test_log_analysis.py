import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from log_analysis import (
    _percentile,
    event_time,
    normalize_env,
    operations,
    percentile_summary,
    split_at,
    summarize,
)


def _e(ts: str, msg: str, **fields):
    return {"ts": ts, "msg": msg, **fields}


# ── operations ────────────────────────────────────────────────────────────────


def test_operations_merges_lines_that_do_not_share_a_request_id():
    """The case this exists for: a search records its query shape and its timings on two
    lines with *different* request_ids, so only time can group them."""
    events = [
        _e("2026-08-23T10:00:00.100Z", "search_performed", query_len=42, request_id="http-1"),
        _e("2026-08-23T10:00:00.300Z", "search_notes", fts_ms=512.5, request_id="mcp-9"),
    ]

    (op,) = operations(events, ["search_performed", "search_notes"])

    assert op["query_len"] == 42
    assert op["fts_ms"] == 512.5
    assert op["_msgs"] == ["search_performed", "search_notes"]


def test_operations_starts_a_new_operation_when_a_msg_repeats():
    events = [
        _e("2026-08-23T10:00:00.000Z", "search_performed", query_len=1),
        _e("2026-08-23T10:00:00.100Z", "search_notes", fts_ms=10),
        _e("2026-08-23T10:00:00.200Z", "search_performed", query_len=2),
        _e("2026-08-23T10:00:00.300Z", "search_notes", fts_ms=20),
    ]

    ops = operations(events, ["search_performed", "search_notes"])

    assert [o["query_len"] for o in ops] == [1, 2]
    assert [o["fts_ms"] for o in ops] == [10, 20]


def test_operations_does_not_merge_across_the_window():
    events = [
        _e("2026-08-23T10:00:00.000Z", "search_performed", query_len=1),
        _e("2026-08-23T10:00:05.000Z", "search_notes", fts_ms=10),
    ]

    ops = operations(events, ["search_performed", "search_notes"], window_s=2.0)

    assert len(ops) == 2
    assert "fts_ms" not in ops[0]
    assert "query_len" not in ops[1]


def test_operations_ignores_other_messages_and_unparseable_timestamps():
    events = [
        _e("2026-08-23T10:00:00.000Z", "search_performed", query_len=1),
        _e("2026-08-23T10:00:00.050Z", "http", status=200),
        _e("not-a-timestamp", "search_notes", fts_ms=999),
    ]

    (op,) = operations(events, ["search_performed", "search_notes"])

    assert op["query_len"] == 1
    assert "status" not in op
    assert "fts_ms" not in op


def test_operations_on_no_matching_events():
    assert operations([_e("2026-08-23T10:00:00Z", "http")], ["search_notes"]) == []


# ── split_at ──────────────────────────────────────────────────────────────────


def test_split_at_partitions_on_the_boundary():
    events = [
        _e("2026-08-22T19:00:00Z", "x", v=1),
        _e("2026-08-22T21:00:00Z", "x", v=2),
    ]

    before, after = split_at(events, "2026-08-22T19:59:08Z")

    assert [e["v"] for e in before] == [1]
    assert [e["v"] for e in after] == [2]


def test_split_at_drops_events_without_a_usable_timestamp():
    before, after = split_at([{"msg": "x"}], "2026-08-22T19:59:08Z")

    assert before == [] and after == []


# ── summaries ─────────────────────────────────────────────────────────────────


def test_percentile_nearest_rank():
    vals = [float(v) for v in range(1, 101)]  # 1..100 sorted
    assert _percentile(vals, 50) == 50
    assert _percentile(vals, 95) == 95
    assert _percentile(vals, 99) == 99
    assert _percentile([], 50) is None
    assert _percentile([42.0], 95) == 42.0


def test_summarize_sorts_before_measuring():
    """summarize takes unsorted input; _percentile takes sorted."""
    s = summarize([30.0, 10.0, 20.0])

    assert s["count"] == 3
    assert s["p50"] == 20.0
    assert s["max"] == 30.0


def test_summarize_of_nothing_reports_no_values_rather_than_zero():
    """A missing measurement must not read as a fast one."""
    assert summarize([]) == {"count": 0, "p50": None, "p95": None, "p99": None, "max": None}


def test_percentile_summary_groups_and_ignores_nonnumeric():
    events = [
        {"tool": "search", "duration_ms": 10},
        {"tool": "search", "duration_ms": 30},
        {"tool": "save", "duration_ms": 100},
        {"tool": "search", "duration_ms": "oops"},  # ignored: non-numeric
        {"duration_ms": 5},  # ignored: no group key
        {"tool": "flag", "duration_ms": True},  # ignored: bool
    ]
    s = percentile_summary(events, "duration_ms", "tool")
    assert s["search"]["count"] == 2
    assert "flag" not in s
    assert s["save"]["p50"] == 100


# ── small helpers ─────────────────────────────────────────────────────────────


def test_normalize_env_accepts_the_english_aliases():
    assert normalize_env("prod") == "produkcja"
    assert normalize_env("production") == "produkcja"
    assert normalize_env("dev") == "develop"
    assert normalize_env("produkcja") == "produkcja"


def test_event_time_returns_none_instead_of_raising():
    assert event_time({"ts": "nope"}) is None
    assert event_time({}) is None
    assert event_time({"ts": "2026-08-23T10:00:00Z"}) is not None
