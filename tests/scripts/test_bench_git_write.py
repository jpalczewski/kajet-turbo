import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

from bench_git_write import (
    NOTE_ROUTE,
    LogTail,
    parse_note_create_spans,
    render_table,
    summarize_spans,
)


def _http(status: int = 201, method: str = "POST", path: str = NOTE_ROUTE, **fields) -> str:
    return json.dumps({"msg": "http", "method": method, "path": path, "status": status, **fields})


def test_parse_keeps_only_successful_single_note_creates():
    lines = [
        "INFO:     Uvicorn running on http://127.0.0.1:8766",  # non-JSON noise
        '{"msg": "http", "method": "POST", "path": "/api/login", "status": 200}',
        _http(status=409),  # conflict — not a write
        _http(method="GET"),
        _http(path="/api/workspaces/{name}/notes/batch"),  # batch is seed traffic
        '{"truncated": ',  # partial line at the tail of the file
        _http(duration_ms=19, git_ms=12.7, db_ms=3.3),
        _http(duration_ms=21, git_ms=14.0, db_ms=3.9),
    ]
    spans = parse_note_create_spans(lines)
    assert [s["duration_ms"] for s in spans] == [19, 21]


def test_summarize_medians_present_fields_and_zero_fills_missing():
    spans = [
        {"duration_ms": 10, "git_ms": 8.0, "db_ms": 1.0},
        {"duration_ms": 20, "git_ms": 12.0, "db_ms": 3.0},
        {"duration_ms": 30, "db_ms": 5.0},  # no git_ms: counts as 0 for the median
    ]
    summary = summarize_spans(spans)
    assert summary == {"duration_ms": 20, "git_ms": 8.0, "db_ms": 3.0}
    assert "git_lock_wait_ms" not in summary  # never reported → not invented
    assert summarize_spans([]) == {}


def test_log_tail_returns_only_lines_appended_since_last_read(tmp_path):
    log = tmp_path / "server.log"
    log.write_text(_http(duration_ms=1) + "\n")
    tail = LogTail(log)
    tail.discard()  # seed traffic: skip what is already there
    assert tail.new_spans() == []
    with log.open("a") as fh:
        fh.write(_http(duration_ms=2) + "\n" + _http(duration_ms=3) + "\n")
    assert [s["duration_ms"] for s in tail.new_spans()] == [2, 3]
    assert tail.new_spans() == []


def test_render_table_lists_every_scenario_with_server_split():
    result = {
        "label": "before",
        "python": "3.14.3",
        "free_threading": True,
        "scenarios": {
            "note_create@c1/100files": {
                "files": 100,
                "latency_ms": {"p50": 22.0, "p95": 25.0},
                "rps": 45.0,
                "errors": 0,
                "server_ms": {"duration_ms": 22, "git_ms": 14.8, "db_ms": 3.8},
            },
            "note_create@c8/100files": {
                "files": 120,
                "latency_ms": None,  # all errors
                "rps": 0,
                "errors": 20,
                "server_ms": {},
            },
        },
    }
    table = render_table(result)
    assert "note_create@c1/100files" in table and "note_create@c8/100files" in table
    assert "| 14.8 |" in table and "| 3.8 |" in table
    assert "| - |" in table  # missing latency / server fields render as dashes
    assert table.rstrip().endswith("| 20 |")
