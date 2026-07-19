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
