import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "analyze_logs", Path(__file__).parent.parent.parent / "scripts" / "analyze-logs.py"
)
analyze_logs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(analyze_logs)


def test_default_source_is_loki(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["analyze-logs.py"])
    args = analyze_logs.parse_args()
    assert args.source == "loki"


def test_positional_log_path_implies_docker_logs(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["analyze-logs.py", "ops/logs/some.log"])
    args = analyze_logs.parse_args()
    assert args.source == "docker-logs"


def test_explicit_source_docker_logs(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["analyze-logs.py", "--source", "docker-logs"])
    args = analyze_logs.parse_args()
    assert args.source == "docker-logs"


def test_default_since_is_24h(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["analyze-logs.py"])
    args = analyze_logs.parse_args()
    assert args.since == "24h"


def test_source_loki_with_log_path_errors(monkeypatch, capsys):
    import pytest

    monkeypatch.setattr(sys, "argv", ["analyze-logs.py", "ops/logs/some.log", "--source", "loki"])
    with pytest.raises(SystemExit) as exc_info:
        analyze_logs.parse_args()
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert (
        "a log file path was given but --source loki was also specified; drop one of the two"
        in captured.err
    )


def test_percentile_nearest_rank():
    vals = [float(v) for v in range(1, 101)]  # 1..100 sorted
    assert analyze_logs._percentile(vals, 50) == 50
    assert analyze_logs._percentile(vals, 95) == 95
    assert analyze_logs._percentile(vals, 99) == 99
    assert analyze_logs._percentile([], 50) is None
    assert analyze_logs._percentile([42.0], 95) == 42.0


def test_percentile_summary_groups_and_ignores_nonnumeric():
    events = [
        {"tool": "search", "duration_ms": 10},
        {"tool": "search", "duration_ms": 30},
        {"tool": "save", "duration_ms": 100},
        {"tool": "search", "duration_ms": "oops"},  # ignored: non-numeric
        {"duration_ms": 5},  # ignored: no group key
        {"tool": "flag", "duration_ms": True},  # ignored: bool
    ]
    s = analyze_logs.percentile_summary(events, "duration_ms", "tool")
    assert s["search"]["count"] == 2
    assert "flag" not in s
    assert s["save"]["p50"] == 100


def test_percentiles_in_modes_and_args(monkeypatch):
    assert "percentiles" in analyze_logs.MODES
    monkeypatch.setattr(
        sys, "argv", ["analyze-logs.py", "x.log", "--mode", "percentiles", "--pct-field", "db_ms"]
    )
    args = analyze_logs.parse_args()
    assert args.mode == "percentiles"
    assert args.pct_field == "db_ms"
    assert args.group_by == "tool"
