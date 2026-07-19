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
async def test_middleware_maps_service_errors_from_mounted_tools(exc_type):
    """Root-server middleware must map SERVICE_ERRORS raised by tools of a
    MOUNTED sub-server — that mirrors the build_mcp assembly (root mounts
    notes/workspaces sub-servers)."""
    inner = FastMCP("inner")

    @inner.tool
    def explode() -> str:
        raise exc_type("boom-mapped")

    root = FastMCP("root")
    root.add_middleware(ServiceErrorMiddleware())
    root.mount(inner)

    async with Client(root) as client:
        with pytest.raises(ToolError, match="boom-mapped"):
            await client.call_tool("explode")


def test_service_errors_tuple_is_exact():
    assert (GitError, ValueError, FileNotFoundError, FileExistsError) == SERVICE_ERRORS


def test_check_batch_rejects_empty():
    with pytest.raises(ToolError, match=r"note_ids nie może być puste\."):
        check_batch([], "note_ids", "note_id")


def test_check_batch_rejects_oversized_with_exact_message():
    with pytest.raises(ToolError, match=r"Maksymalnie 50 edycji na wywołanie \(podano 51\)\."):
        check_batch(list(range(51)), "edits", "edycji")


def test_check_batch_accepts_at_limit():
    check_batch(list(range(50)), "deletes", "usunięć")  # no raise


def test_require_found_passes_value_through():
    assert require_found({"x": 1}, "id1") == {"x": 1}


def test_require_found_raises_on_none():
    with pytest.raises(ToolError, match=r"Notatka id1 nie znaleziona\."):
        require_found(None, "id1")
