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
