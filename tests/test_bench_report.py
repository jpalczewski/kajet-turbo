import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from bench_report import build_report  # noqa: E402


def _result(label: str, p95: float, rps: float, errors: int = 0, latency_ms=None) -> dict:
    if latency_ms is False:
        # Simulate all-errors scenario: latency_ms is None
        lat = None
    else:
        lat = {"p50": 1.0, "p95": p95, "p99": 9.0, "mean": 2.0, "count": 300}
    return {
        "label": label,
        "python": "3.14.2",
        "free_threading": False,
        "scenarios": {
            "note_html@c10": {"latency_ms": lat,
                              "rps": rps, "errors": errors},
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


def test_build_report_handles_missing_scenario_and_errors():
    # Second result lacks the scenario entirely
    r1 = _result("run1", 5.0, 100.0)
    r2 = {
        "label": "run2",
        "python": "3.14.2",
        "free_threading": False,
        "scenarios": {},  # scenario missing
    }
    report = build_report([r1, r2])
    # Missing scenario cell should render as "–"
    assert "–" in report

    # Third case: errors > 0 should render ⚠
    r3 = _result("run3", 5.0, 50.0, errors=7)
    report2 = build_report([r3])
    assert "⚠" in report2
    assert "7" in report2

    # Fourth case: latency_ms is None (all requests failed) should render "–" for latency cells
    r4 = _result("run4", 0.0, 0.0, errors=10, latency_ms=False)
    report3 = build_report([r4])
    assert "–" in report3
    assert "⚠" in report3
