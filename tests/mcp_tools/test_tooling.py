import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from fastmcp import Client, FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError

from kajet_turbo.mcp.tooling import (
    SERVICE_ERRORS,
    ToolDispatchMiddleware,
    check_batch,
    require_found,
)
from kajet_turbo.repositories.git import GitError
from tests.helpers import read_log_entries


@pytest.mark.parametrize("exc_type", [GitError, ValueError, FileNotFoundError, FileExistsError])
async def test_middleware_maps_service_errors_to_exact_tool_error(exc_type):
    """A SERVICE_ERRORS member must reach the caller as its own message, with nothing
    prepended — the contract mcp/CLAUDE.md makes to the calling model.

    fastmcp's core has already replaced the exception by the time the middleware runs:
    call_next() resolves to call_tool(run_middleware=False), whose try/except wraps
    anything surviving tool._run() into ToolError(f"Error calling tool {name!r}: {e}")
    inside the frame the middleware is awaiting. What survives that is `__cause__`, and
    recovering the original from it is what lets one middleware own the mapping that
    used to need a wrapper under every single @srv.tool.

    Assert the *exact* message, not a substring: a `match=` check would pass just as
    happily on fastmcp's verbose wrapper text, which is the specific regression this
    guards (it once went unnoticed for exactly that reason)."""
    root = FastMCP("root")
    root.add_middleware(ToolDispatchMiddleware())

    @root.tool
    async def explode() -> str:
        raise exc_type("boom-mapped")

    async with Client(root) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("explode")

    assert str(exc_info.value) == "boom-mapped"


def test_service_errors_tuple_is_exact():
    assert (GitError, ValueError, FileNotFoundError, FileExistsError) == SERVICE_ERRORS


def test_check_batch_rejects_empty():
    with pytest.raises(ToolError, match=r"note_ids cannot be empty\."):
        check_batch([], "note_ids", "note_id")


def test_check_batch_rejects_oversized_with_exact_message():
    with pytest.raises(ToolError, match=r"At most 50 edycji per call \(got 51\)\."):
        check_batch(list(range(51)), "edits", "edycji")


def test_check_batch_accepts_at_limit():
    check_batch(list(range(50)), "deletes", "usunięć")  # no raise


def test_require_found_passes_value_through():
    assert require_found({"x": 1}, "id1") == {"x": 1}


def test_require_found_raises_on_none():
    with pytest.raises(ToolError, match=r"Note not found: note_id=id1"):
        require_found(None, "id1")


async def test_middleware_logs_tool_error_from_dependency_resolution(capsys):
    """A ToolError raised while resolving a Depends default (like WORKSPACE_TARGET) is
    raised before the tool function runs at all, so no wrapper around that function could
    ever see it. ToolDispatchMiddleware sees dependency resolution and the tool body
    alike, which is what makes it the one seam able to log this case (issue #71)."""
    from fastmcp.dependencies import Depends

    from kajet_turbo.log import logger, setup_logging
    from tests.helpers import entries_named, read_log_entries

    setup_logging()

    def _no_workspace_access() -> str:
        raise ToolError("Workspace not accessible: no-such-ws")

    root = FastMCP("root")
    root.add_middleware(ToolDispatchMiddleware())

    @root.tool
    async def needs_workspace(ws: str = Depends(_no_workspace_access)) -> str:
        return ws

    with logger.contextualize(request_id="test-req"):
        async with Client(root) as client:
            with pytest.raises(ToolError, match="not accessible"):
                await client.call_tool("needs_workspace")

    (entry,) = entries_named(read_log_entries(capsys), "needs_workspace")
    assert entry["level"] == "error"
    assert entry["error_type"] == "ToolError"
    assert "not accessible" in entry["error_msg"]
    assert "duration_ms" in entry


async def test_middleware_logs_body_raised_tool_error_exactly_once(capsys):
    """A ToolError raised on purpose inside a tool body reaches the middleware with
    `__cause__` unset — that is what distinguishes it from one fastmcp wrapped around
    something else — and must produce exactly one line, not two, not zero (issue #71)."""
    from kajet_turbo.log import logger, setup_logging
    from tests.helpers import entries_named, read_log_entries

    setup_logging()

    root = FastMCP("root")
    root.add_middleware(ToolDispatchMiddleware())

    @root.tool
    async def explode_in_body() -> str:
        raise ToolError("boom-body")

    with logger.contextualize(request_id="test-req"):
        async with Client(root) as client:
            with pytest.raises(ToolError, match="boom-body"):
                await client.call_tool("explode_in_body")

    entries = entries_named(read_log_entries(capsys), "explode_in_body")
    assert len(entries) == 1
    assert entries[0]["error_type"] == "ToolError"


async def test_middleware_logs_unexpected_exception_under_its_own_type(capsys):
    """A RuntimeError is not a SERVICE_ERRORS member, so it stays wrapped in fastmcp's
    generic ToolError on the way out — but the record must name the real type, which
    only `__cause__` still knows. One line, and not "ToolError" (issue #71)."""
    from kajet_turbo.log import logger, setup_logging
    from tests.helpers import entries_named, read_log_entries

    setup_logging()

    root = FastMCP("root")
    root.add_middleware(ToolDispatchMiddleware())

    @root.tool
    async def explode_with_runtime_error() -> str:
        raise RuntimeError("boom-runtime")

    with logger.contextualize(request_id="test-req"):
        async with Client(root) as client:
            with pytest.raises(ToolError, match="boom-runtime"):
                await client.call_tool("explode_with_runtime_error")

    entries = entries_named(read_log_entries(capsys), "explode_with_runtime_error")
    assert len(entries) == 1
    assert entries[0]["error_type"] == "RuntimeError"


async def test_nested_mount_tool_error_logs_once_not_per_mount_level(capsys):
    """A tool error unwinds through every mount() level it passes through, and each
    level's own call_tool() independently logged "Error calling tool" — one real
    failure became N identical lines, one per level (issue #36). root -> mid -> leaf
    mirrors build_mcp's real depth (root mounts notes/workspaces, which mount their
    own sub-servers).

    Those lines are now dropped outright: the middleware owns the record for a tool
    call, so fastmcp's duplicates of it are filtered at the intercept handler rather
    than deduplicated after the fact. What is left is the one record the middleware
    emits, on the root server, however deep the tool actually lives."""
    from kajet_turbo.log import logger, setup_logging
    from tests.helpers import read_log_entries

    setup_logging()

    leaf = FastMCP("leaf")

    @leaf.tool
    def explode() -> str:
        raise ToolError("boom-nested")

    mid = FastMCP("mid")
    mid.mount(leaf)
    root = FastMCP("root")
    root.add_middleware(ToolDispatchMiddleware())
    root.mount(mid)

    with logger.contextualize(request_id="test-req"):
        async with Client(root) as client:
            with pytest.raises(ToolError, match="boom-nested"):
                await client.call_tool("explode")

    (entry,) = _records_mentioning(read_log_entries(capsys), "explode")
    assert entry["tool"] == "explode"
    assert entry["error_type"] == "ToolError"


# --- One record per tool call (issue #249) -------------------------------------------
#
# The dispatch contract: every tools/call outcome produces exactly one structured
# record, emitted by ToolDispatchMiddleware, carrying `tool`, `duration_ms` and the
# correlation ids — and never the call's arguments. The four cases below are the four
# ways a call can end, and each was historically logged by a different seam (or by
# none at all), which is what #71/#238 kept re-opening.


def _records_mentioning(entries, tool: str) -> list[dict]:
    """Every record that talks about `tool`, however it names it.

    Not `entries_named`: a duplicate does not have to arrive under the tool's own
    name. fastmcp's core logs its own "Error calling tool 'x'" line through the
    intercepted stdlib logger, so a second record shows up under a *different* msg —
    exactly the double this contract forbids, and exactly what a name-equality filter
    would miss.
    """
    return [e for e in entries if tool in json.dumps(e)]


@asynccontextmanager
async def _dispatch_probe():
    """A root server carrying the real middleware plus one tool per failure mode."""
    from kajet_turbo.log import logger, setup_logging

    setup_logging()

    def _denied() -> str:
        raise ToolError("Workspace not accessible: ws-1")

    root = FastMCP("root")
    root.add_middleware(ToolDispatchMiddleware())

    @root.tool
    async def spine_ok(count: int) -> str:
        return f"ok:{count}"

    @root.tool
    async def spine_dep(ws: str = Depends(_denied)) -> str:
        return ws

    @root.tool
    async def spine_service() -> str:
        raise ValueError("Mode 'replace_text' does not take content.")

    with logger.contextualize(request_id="test-req"):
        async with Client(root) as client:
            yield client


async def test_dispatch_logs_exactly_one_record_on_success(capsys):
    async with _dispatch_probe() as client:
        result = await client.call_tool("spine_ok", {"count": 1})
    assert result.content[0].text == "ok:1"

    (entry,) = _records_mentioning(read_log_entries(capsys), "spine_ok")
    assert entry["tool"] == "spine_ok"
    assert isinstance(entry["duration_ms"], int | float)
    # The live per-call id, not the ambient "test-req" the probe bound around the whole
    # session: reading correlation ids off the dispatch Context rather than the
    # contextvars is what keeps a persistent session from stamping every tool call with
    # the initialize request's id (issue #71).
    assert entry["request_id"] and entry["request_id"] != "test-req"


async def test_dispatch_logs_exactly_one_record_on_dependency_failure(capsys):
    """A Depends failure resolves before the tool body runs — the seam that #71 found
    unlogged and #238 fixed. Guard it here so the consolidation cannot silently lose it.
    """
    async with _dispatch_probe() as client:
        with pytest.raises(ToolError, match="not accessible"):
            await client.call_tool("spine_dep")

    (entry,) = _records_mentioning(read_log_entries(capsys), "spine_dep")
    assert entry["tool"] == "spine_dep"
    assert entry["level"] == "error"
    assert entry["request_id"]
    assert "duration_ms" in entry


async def test_dispatch_logs_exactly_one_record_on_validation_failure(capsys):
    """Argument validation fails inside fastmcp's core, before any tool code runs, and
    surfaces as fastmcp's own ValidationError rather than a ToolError.

    The rejected value is the payload — for a real tool that is a note body — so the
    record may carry the failing type and field, never the value itself.
    """
    async with _dispatch_probe() as client:
        with pytest.raises(ToolError):
            await client.call_tool("spine_ok", {"count": "not-a-number"})

    (entry,) = _records_mentioning(read_log_entries(capsys), "spine_ok")
    assert entry["tool"] == "spine_ok"
    # A bad call is the caller's mistake, not a server fault — warning, not error.
    assert entry["level"] == "warning"
    assert entry["error_type"] == "ValidationError"
    assert entry["rejected_params"] == ["count"]
    assert entry["request_id"]
    assert "duration_ms" in entry
    assert "not-a-number" not in json.dumps(entry)


async def test_dispatch_logs_exactly_one_record_on_service_error(capsys):
    """A SERVICE_ERRORS member raised by a service reaches the caller verbatim — the
    contract in mcp/CLAUDE.md — and is logged once, under its original type."""
    async with _dispatch_probe() as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("spine_service")

    assert str(exc_info.value) == "Mode 'replace_text' does not take content."

    (entry,) = _records_mentioning(read_log_entries(capsys), "spine_service")
    assert entry["tool"] == "spine_service"
    assert entry["error_type"] == "ValueError"
    assert entry["request_id"]
    assert "duration_ms" in entry


async def test_dispatch_logs_slow_call_at_warning(capsys, monkeypatch):
    """The completion record doubles as the slow-call alert: same fields, raised to
    WARNING once the call crosses SLOW_TOOL_MS. Moving the threshold from the per-tool
    wrapper into the middleware widened what it measures — dependency resolution and
    argument validation now count toward it, which is the point, since NOTE_TARGET
    resolution does real database work."""
    import kajet_turbo.mcp.tooling as tooling
    from kajet_turbo.log import setup_logging

    setup_logging()
    monkeypatch.setattr(tooling, "_SLOW_TOOL_MS", 1.0)

    root = FastMCP("root")
    root.add_middleware(ToolDispatchMiddleware())

    @root.tool
    async def slow_tool() -> str:
        await asyncio.sleep(0.02)
        return "ok"

    async with Client(root) as client:
        await client.call_tool("slow_tool")

    (entry,) = _records_mentioning(read_log_entries(capsys), "slow_tool")
    assert entry["level"] == "warning"
    assert entry["duration_ms"] >= 1


async def test_dispatch_logs_fast_call_at_info(capsys, monkeypatch):
    import kajet_turbo.mcp.tooling as tooling
    from kajet_turbo.log import setup_logging

    setup_logging()
    monkeypatch.setattr(tooling, "_SLOW_TOOL_MS", 10_000.0)

    root = FastMCP("root")
    root.add_middleware(ToolDispatchMiddleware())

    @root.tool
    async def quick_tool() -> str:
        return "ok"

    async with Client(root) as client:
        await client.call_tool("quick_tool")

    (entry,) = _records_mentioning(read_log_entries(capsys), "quick_tool")
    assert entry["level"] == "info"


async def test_dispatch_never_logs_a_pydantic_result_payload(capsys):
    """The other exception that carries a payload: a model validating *inside* a tool.

    fastmcp converts argument-validation failures into its own ValidationError, but a
    pydantic error raised by the tool's own code (SavedNoteResult over a result about to
    be returned) propagates untouched — and renders the rejected input, here the note,
    into str(). It must be logged by path, like the argument case."""
    from mcp.shared.exceptions import MCPError
    from pydantic import BaseModel

    from kajet_turbo.log import setup_logging

    setup_logging()
    secret = "a private note body"

    class Saved(BaseModel):
        sha: int

    root = FastMCP("root")
    root.add_middleware(ToolDispatchMiddleware())

    @root.tool
    async def bad_result() -> str:
        Saved.model_validate({"sha": secret})
        return "unreachable"

    # Not a ToolError: an unconverted pydantic error leaves fastmcp's core as a
    # JSON-RPC "Invalid request parameters" (-32602) rather than a tool result. That
    # is fastmcp's call, and the record is what this test is about either way.
    async with Client(root) as client:
        with pytest.raises(MCPError):
            await client.call_tool("bad_result")

    entries = read_log_entries(capsys)
    assert secret not in json.dumps(entries)
    (entry,) = _records_mentioning(entries, "bad_result")
    assert entry["error_type"] == "PydanticValidationError"
    assert entry["rejected_params"] == ["sha"]
