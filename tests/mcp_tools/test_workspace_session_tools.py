"""Workspace listing/activation, and cross-session workspace persistence & scoping."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError


async def test_list_workspaces(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        result = await client.call_tool("list_workspaces")
    names = [w["name"] for w in json.loads(result.content[0].text)["workspaces"]]
    assert "test-ws" in names


async def test_activate_workspace(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        result = await client.call_tool("activate_workspace", {"name": "test-ws"})
    assert "test-ws" in result.content[0].text


async def test_activate_nonexistent_workspace(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("activate_workspace", {"name": "nie-istnieje"})


# --- active workspace scope: claude.ai conversations share user/client, not MCP session ---


async def test_fresh_authenticated_session_inherits_workspace_within_ttl(
    authed_workspaces_dir, authed_mcp_server
):
    """The claude.ai connector opens a fresh MCP session per tool call (upstream bug: it
    never echoes back Mcp-Session-Id), so session-scoped state can't survive to the next
    call. The per-user DB fallback bridges that gap for a bounded TTL window."""
    mcp, _ = authed_mcp_server
    async with Client(mcp) as client_a:
        await client_a.call_tool("activate_workspace", {"name": "test-ws"})

    async with Client(mcp) as client_b:
        result = await client_b.call_tool("save_note", {"title": "Inherited", "content": "body"})
    assert "error" not in json.loads(result.content[0].text)


async def test_two_authenticated_sessions_keep_separate_workspaces(
    authed_workspaces_dir, authed_mcp_server, git_workspace_factory
):
    mcp, _ = authed_mcp_server
    git_workspace_factory("workspaces/u1/drugi-ws")
    authed_mcp_server.workspace_repo.grant_access("u1", "drugi-ws")

    async with Client(mcp) as client_a:
        await client_a.call_tool("activate_workspace", {"name": "test-ws"})
        first = json.loads(
            (await client_a.call_tool("save_note", {"title": "First", "content": "body"}))
            .content[0]
            .text
        )["note_id"]

    async with Client(mcp) as client_b:
        await client_b.call_tool("activate_workspace", {"name": "drugi-ws"})
        second = json.loads(
            (await client_b.call_tool("save_note", {"title": "Second", "content": "body"}))
            .content[0]
            .text
        )["note_id"]

    async with Client(mcp) as client_a_again:
        await client_a_again.call_tool("activate_workspace", {"name": "test-ws"})
        first_note = await client_a_again.call_tool("get_note", {"note_id": first})
        with pytest.raises(ToolError):
            await client_a_again.call_tool("get_note", {"note_id": second})

    assert "First" in first_note.content[0].text


async def test_anon_no_cross_session_persistence(workspaces_dir, mcp_server):
    """Unauthenticated sessions get no cross-session persistence (IDOR-safe)."""
    mcp, _ = mcp_server
    async with Client(mcp) as client_a:
        await client_a.call_tool("activate_workspace", {"name": "test-ws"})

    async with Client(mcp) as client_b:
        with pytest.raises(ToolError, match="activate_workspace"):
            await client_b.call_tool("save_note", {"title": "Should fail", "content": "body"})


async def test_authenticated_session_writes_to_user_scoped_path(
    authed_workspaces_dir, authed_mcp_server
):
    mcp, _ = authed_mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool("save_note", {"title": "Scoped note", "content": "body"})

    ws_path = authed_workspaces_dir / "u1" / "test-ws"
    files = [p for p in ws_path.rglob("*.md") if ".git" not in str(p)]
    assert len(files) == 1


async def test_search_all_scope_after_session_activation(authed_workspaces_dir, authed_mcp_server):
    mcp, _ = authed_mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        search_result = await client.call_tool(
            "search_notes", {"query": "anything", "workspace": "all"}
        )
    assert not search_result.is_error


async def test_same_session_fast_path_unchanged(authed_workspaces_dir, authed_mcp_server):
    """Single-session activate+save still works (Claude Code path, no DB needed on read)."""
    mcp, _ = authed_mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool(
            "save_note", {"title": "Same session", "content": "body"}
        )
    payload = json.loads(save_result.content[0].text)
    assert "error" not in payload
    assert len(payload["note_id"]) > 0
