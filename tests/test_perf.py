import json

from kajet_turbo import perf


def test_record_incr_timed_are_noop_without_span():
    perf.record("git_ms", 12.0)
    perf.incr("chunks", 3)
    with perf.timed("db_ms"):
        pass
    assert perf.current() is None


def test_perf_span_accumulates():
    with perf.perf_span() as span:
        perf.record("git_ms", 10)
        perf.record("git_ms", 5)
        perf.incr("chunks", 2)
        perf.incr("chunks")
        with perf.timed("db_ms"):
            pass
    assert span.fields["git_ms"] == 15
    assert span.fields["chunks"] == 3
    assert "db_ms" in span.fields


def test_perf_span_disabled_yields_none(monkeypatch):
    monkeypatch.setattr(perf, "_ENABLED", False)
    with perf.perf_span() as span:
        perf.record("git_ms", 99)
        assert span is None
    assert perf.current() is None


def test_span_resets_after_block():
    with perf.perf_span():
        assert perf.current() is not None
    assert perf.current() is None


async def test_logged_tool_merges_span_fields(capsys):
    from kajet_turbo.log import logged_tool, setup_logging

    setup_logging()

    @logged_tool
    async def my_tool() -> str:
        perf.record("git_ms", 7)
        perf.incr("chunks", 4)
        return "ok"

    await my_tool()

    captured = capsys.readouterr()
    entry = json.loads([ln for ln in captured.err.strip().split("\n") if ln][-1])
    assert entry["tool"] == "my_tool"
    assert entry["git_ms"] == 7
    assert entry["chunks"] == 4
