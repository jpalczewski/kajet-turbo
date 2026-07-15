import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from kajet_turbo.mcp.tooling import SERVICE_ERRORS, ServiceErrorMiddleware
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
