import json
import time

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


def test_peek_returns_running_sum_and_zero_without_span():
    assert perf.peek("db_ms") == 0.0  # no span
    with perf.perf_span():
        assert perf.peek("db_ms") == 0.0
        perf.record("db_ms", 12.0)
        assert perf.peek("db_ms") == 12.0
        perf.record("db_ms", 8.0)
        assert perf.peek("db_ms") == 20.0


def test_excluded_from_subtracts_nested_block_from_span_field():
    with perf.perf_span() as span, perf.timed("db_ms"):
        time.sleep(0.02)
        with perf.excluded_from("db_ms"):
            time.sleep(0.05)
    assert span is not None
    # Outer timed() block adds ~70ms total; excluded_from subtracts its own ~50ms back
    # out, leaving only the non-excluded ~20ms (banded: two independent round(...,1)
    # calls don't cancel exactly).
    assert 10 <= span.fields["db_ms"] < 45


def test_excluded_from_reports_into_local_exclusion_scope_without_a_span():
    assert perf.current() is None
    with perf.local_exclusion_scope() as pop_excluded:
        with perf.excluded_from("db_ms"):
            time.sleep(0.05)
        excluded = pop_excluded("db_ms")
    assert 35 <= excluded < 85
    # pop() clears the ledger entry.
    with perf.local_exclusion_scope() as pop_excluded:
        assert pop_excluded("db_ms") == 0.0


def test_excluded_from_updates_span_and_local_scope_independently():
    with (
        perf.perf_span() as span,
        perf.local_exclusion_scope() as pop_excluded,
        perf.timed("db_ms"),
        perf.excluded_from("db_ms"),
    ):
        time.sleep(0.05)
    assert span is not None
    assert pop_excluded("db_ms") >= 35
    assert span.fields["db_ms"] < 20


async def test_tool_dispatch_merges_span_fields(capsys):
    """The perf span is opened by ToolDispatchMiddleware, so whatever a tool (or a
    repository under it) records into the active span lands on that call's one record."""
    from fastmcp import Client, FastMCP

    from kajet_turbo.log import setup_logging
    from kajet_turbo.mcp.tooling import ToolDispatchMiddleware

    setup_logging()
    server = FastMCP("perf")
    server.add_middleware(ToolDispatchMiddleware())

    @server.tool
    async def my_tool() -> str:
        perf.record("git_ms", 7)
        perf.incr("chunks", 4)
        return "ok"

    async with Client(server) as client:
        await client.call_tool("my_tool")

    captured = capsys.readouterr()
    entries = [json.loads(ln) for ln in captured.err.strip().split("\n") if ln]
    (entry,) = [e for e in entries if e.get("tool") == "my_tool"]
    assert entry["git_ms"] == 7
    assert entry["chunks"] == 4
