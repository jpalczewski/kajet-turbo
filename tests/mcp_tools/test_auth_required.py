"""MCP tools reject callers without a resolvable identity."""

import json
from types import SimpleNamespace

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from kajet_turbo.repositories.git import GitRepository


async def test_tokenless_list_workspaces_rejected(tokenless_mcp_server):
    mcp, _ = tokenless_mcp_server
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Wymagane zalogowanie"):
            await client.call_tool("list_workspaces")


async def test_tokenless_save_note_rejected(tokenless_mcp_server):
    mcp, _ = tokenless_mcp_server
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Wymagane zalogowanie"):
            await client.call_tool("save_note", {"title": "Nope", "content": "body"})


async def test_token_for_unbound_client_rejected(tokenless_mcp_server, monkeypatch):
    """A token that maps to no user (client never completed OAuth) is rejected the same
    way as a missing token."""
    monkeypatch.setattr(
        "kajet_turbo.mcp.context.get_access_token",
        lambda: SimpleNamespace(client_id="ghost"),
    )
    mcp, _ = tokenless_mcp_server
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Wymagane zalogowanie"):
            await client.call_tool("list_workspaces")


async def test_activate_workspace_ignores_ungranted_disk_workspace(workspaces_dir, mcp_server):
    """A workspace directory on disk without a DB grant must not be reachable — there is
    no filesystem-listing fallback anymore."""

    other_ws = workspaces_dir / "other-ws"
    other_ws.mkdir()
    GitRepository.init(str(other_ws))

    mcp, _ = mcp_server
    async with Client(mcp) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("activate_workspace", {"name": "other-ws"})

    data = json.loads(str(exc_info.value))
    assert data["available"] == ["test-ws"]
