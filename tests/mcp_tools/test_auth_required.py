"""MCP tools reject callers without a resolvable identity."""

from types import SimpleNamespace

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from kajet_turbo.repositories.git import GitRepository


async def test_tokenless_list_workspaces_rejected(tokenless_mcp_server):
    mcp, _ = tokenless_mcp_server
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Authentication required"):
            await client.call_tool("list_workspaces")


async def test_tokenless_save_note_rejected(tokenless_mcp_server):
    mcp, _ = tokenless_mcp_server
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Authentication required"):
            await client.call_tool(
                "save_note", {"title": "Nope", "content": "body", "workspace": "test-ws"}
            )


async def test_token_that_maps_to_no_user_is_rejected(tokenless_mcp_server, monkeypatch):
    """A token with no owner — never issued, or predating the user_id column — is
    rejected the same way as a missing token, rather than falling back to whoever last
    authorized the client."""
    monkeypatch.setattr(
        "kajet_turbo.mcp.context.get_access_token",
        lambda: SimpleNamespace(client_id="ghost", token="at-never-issued"),
    )
    mcp, _ = tokenless_mcp_server
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Authentication required"):
            await client.call_tool("list_workspaces")


async def test_ungranted_disk_workspace_is_unreachable(workspaces_dir, mcp_server):
    """A workspace directory on disk without a DB grant must not be reachable through a
    workspace-scoped tool — there is no filesystem-listing fallback."""

    other_ws = workspaces_dir / "other-ws"
    other_ws.mkdir()
    GitRepository.init(str(other_ws))

    mcp, _ = mcp_server
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Workspace not accessible: other-ws"):
            await client.call_tool("list_folders", {"workspace": "other-ws"})
