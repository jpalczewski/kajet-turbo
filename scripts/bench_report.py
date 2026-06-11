"""Build a markdown comparison table from bench.py JSON result files.

Usage: uv run python scripts/bench_report.py results/a.json results/b.json > report.md
The first file is the baseline for delta columns.
"""
import json
import sys


def build_report(results: list[dict]) -> str:
    labels = [r["label"] for r in results]
    scenario_names: list[str] = []
    for r in results:
        for name in r["scenarios"]:
            if name not in scenario_names:
                scenario_names.append(name)

    lines = ["# Benchmark report", ""]
    lines.append("| run | python | free-threading |")
    lines.append("|---|---|---|")
    for r in results:
        lines.append(f"| {r['label']} | {r['python']} | {r['free_threading']} |")
    lines.append("")

    header = "| scenario | metric | " + " | ".join(labels) + " | delta vs first |"
    sep = "|---" * (len(labels) + 3) + "|"
    lines += [header, sep]
    for name in scenario_names:
        for metric, getter in (
            ("p50 ms", lambda s: s["latency_ms"]["p50"]),
            ("p95 ms", lambda s: s["latency_ms"]["p95"]),
            ("p99 ms", lambda s: s["latency_ms"]["p99"]),
            ("rps", lambda s: s["rps"]),
        ):
            cells, values = [], []
            for r in results:
                s = r["scenarios"].get(name)
                cells.append("–" if s is None else f"{getter(s)}")
                values.append(None if s is None else getter(s))
            delta = "–"
            if values[0] and values[-1] is not None:
                pct = (values[-1] - values[0]) / values[0] * 100
                delta = f"{pct:+.1f}%"
            lines.append(f"| {name} | {metric} | " + " | ".join(cells) + f" | {delta} |")
    return "\n".join(lines)


def main() -> None:
    results = [json.loads(open(p).read()) for p in sys.argv[1:]]
    print(build_report(results))


if __name__ == "__main__":
    main()
