import importlib.util
import sys
from pathlib import Path

# The CLI imports its own helpers as a bare `log_analysis`, which resolves for free when it
# runs as a script but not when importlib loads it from here.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

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


# _percentile and percentile_summary moved to scripts/log_analysis.py along with the code;
# their tests live in test_log_analysis.py now.


def test_percentiles_in_modes_and_args(monkeypatch):
    assert "percentiles" in analyze_logs.MODES
    monkeypatch.setattr(
        sys, "argv", ["analyze-logs.py", "x.log", "--mode", "percentiles", "--pct-field", "db_ms"]
    )
    args = analyze_logs.parse_args()
    assert args.mode == "percentiles"
    assert args.pct_field == "db_ms"
    assert args.group_by == "tool"


def test_compare_applies_msg_filter_and_ignores_booleans(monkeypatch, capsys):
    events = [
        {"ts": "2026-01-01T00:00:00Z", "msg": "wanted", "duration_ms": 10},
        {"ts": "2026-01-01T00:00:01Z", "msg": "other", "duration_ms": 1000},
        {"ts": "2026-01-01T00:00:02Z", "msg": "wanted", "duration_ms": True},
        {"ts": "2026-01-01T00:00:03Z", "msg": "wanted", "duration_ms": 20},
    ]
    monkeypatch.setattr(analyze_logs, "load_events", lambda **kwargs: events)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze-logs.py",
            "capture.log",
            "--msg",
            "wanted",
            "--compare-at",
            "2026-01-01T00:00:02Z",
        ],
    )

    analyze_logs.main()

    output = capsys.readouterr().out
    count_line = next(line for line in output.splitlines() if line.strip().startswith("count"))
    assert "1.0" in count_line
    assert "1000" not in output


def test_docker_banner_names_resolved_latest_file(monkeypatch, capsys, tmp_path):
    resolved = tmp_path / "actual-capture.jsonl"
    monkeypatch.setattr(analyze_logs, "latest_log", lambda role, env: resolved)
    monkeypatch.setattr(analyze_logs, "load_events", lambda **kwargs: [])
    monkeypatch.setattr(
        sys, "argv", ["analyze-logs.py", "--source", "docker-logs", "--mode", "http"]
    )

    analyze_logs.main()

    assert f"→ {resolved}" in capsys.readouterr().out
