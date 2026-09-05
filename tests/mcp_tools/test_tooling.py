import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kajet_turbo.mcp.tooling import (
    SERVICE_ERRORS,
    ServiceErrorMiddleware,
    check_batch,
    require_found,
)
from kajet_turbo.repositories.git import GitError


@pytest.mark.parametrize("exc_type", [GitError, ValueError, FileNotFoundError, FileExistsError])
async def test_logged_tool_maps_service_errors_to_exact_tool_error(exc_type):
    """logged_tool (log.py), not ServiceErrorMiddleware, is what actually turns a
    SERVICE_ERRORS exception into a clean ToolError: fastmcp's own call_tool() already
    wraps anything surviving tool._run() into ToolError(f"Error calling tool {name!r}:
    {e}") before ServiceErrorMiddleware.on_call_tool's try/except ever sees it —
    call_next() resolves to call_tool(run_middleware=False), whose core-logic
    try/except runs first, inside the frame the middleware is awaiting. logged_tool sits
    directly around the raw coroutine, inside tool._run(), so it is the only seam that
    can still convert before that generic wrapping kicks in.

    Assert the *exact* message, not a substring: a `match=` substring check would
    silently pass even if this fell back to fastmcp's own verbose wrapper text (as the
    predecessor of this test did, unnoticed, when the mapping only lived in the now-dead
    ServiceErrorMiddleware except-clause below)."""
    from kajet_turbo.log import logged_tool

    root = FastMCP("root")
    root.add_middleware(ServiceErrorMiddleware())

    @root.tool
    @logged_tool
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
    """A ToolError raised while resolving a Depends default (like ACTIVE_WORKSPACE) never
    enters logged_tool's try/except, since FastMCP resolves it before the wrapped function
    runs. ServiceErrorMiddleware is the one seam that sees dependency resolution and the
    tool body alike, so it must log this case itself (issue #71)."""
    from fastmcp.dependencies import Depends

    from kajet_turbo.log import logger, setup_logging
    from tests.helpers import entries_named, read_log_entries

    setup_logging()

    def _no_active_workspace() -> str:
        raise ToolError("Wywołaj activate_workspace() najpierw.")

    root = FastMCP("root")
    root.add_middleware(ServiceErrorMiddleware())

    @root.tool
    async def needs_workspace(ws: str = Depends(_no_active_workspace)) -> str:
        return ws

    with logger.contextualize(request_id="test-req"):
        async with Client(root) as client:
            with pytest.raises(ToolError, match="activate_workspace"):
                await client.call_tool("needs_workspace")

    (entry,) = entries_named(read_log_entries(capsys), "needs_workspace")
    assert entry["level"] == "error"
    assert entry["error_type"] == "ToolError"
    assert "activate_workspace" in entry["error_msg"]
    assert "duration_ms" in entry


async def test_middleware_logs_body_raised_tool_error_exactly_once(capsys):
    """A ToolError raised directly inside a @logged_tool-wrapped body used to be logged by
    logged_tool itself; now logged_tool passes it through untouched and the middleware is
    the sole logger — must still be exactly one line, not two, not zero (issue #71)."""
    from kajet_turbo.log import logged_tool, logger, setup_logging
    from tests.helpers import entries_named, read_log_entries

    setup_logging()

    root = FastMCP("root")
    root.add_middleware(ServiceErrorMiddleware())

    @root.tool
    @logged_tool
    async def explode_in_body() -> str:
        raise ToolError("boom-body")

    with logger.contextualize(request_id="test-req"):
        async with Client(root) as client:
            with pytest.raises(ToolError, match="boom-body"):
                await client.call_tool("explode_in_body")

    entries = entries_named(read_log_entries(capsys), "explode_in_body")
    assert len(entries) == 1
    assert entries[0]["error_type"] == "ToolError"


async def test_middleware_does_not_double_log_non_tool_error(capsys):
    """A non-ToolError exception (e.g. RuntimeError) is logged once by logged_tool under
    its own type; fastmcp then wraps it into a ToolError with __cause__ set on its way out.
    The middleware must recognize that wrapping and skip logging it again (issue #71)."""
    from kajet_turbo.log import logged_tool, logger, setup_logging
    from tests.helpers import entries_named, read_log_entries

    setup_logging()

    root = FastMCP("root")
    root.add_middleware(ServiceErrorMiddleware())

    @root.tool
    @logged_tool
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
    level's own call_tool() independently logs "Error calling tool" (fastmcp's
    server.py:1284, exc_info=False) — see issue #36. root -> mid -> leaf mirrors
    build_mcp's real depth (root mounts notes/workspaces, which mount crud/...)."""
    from kajet_turbo.log import logger, setup_logging
    from tests.helpers import entries_named, read_log_entries

    setup_logging()

    leaf = FastMCP("leaf")

    @leaf.tool
    def explode() -> str:
        raise ToolError("boom-nested")

    mid = FastMCP("mid")
    mid.mount(leaf)
    root = FastMCP("root")
    root.mount(mid)

    with logger.contextualize(request_id="test-req"):
        async with Client(root) as client:
            with pytest.raises(ToolError, match="boom-nested"):
                await client.call_tool("explode")

    entries = entries_named(read_log_entries(capsys), "Error calling tool 'explode'")
    assert len(entries) == 1
