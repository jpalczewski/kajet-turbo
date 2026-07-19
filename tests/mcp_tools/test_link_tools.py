"""get_note_links tool coverage."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from kajet_turbo.repositories.git import GitRepository


async def test_get_note_links(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        target_id = json.loads(
            (await client.call_tool("save_note", {"title": "Target", "content": "content"}))
            .content[0]
            .text
        )["note_id"]
        source_id = json.loads(
            (await client.call_tool("save_note", {"title": "Source", "content": "see [[Target]]"}))
            .content[0]
            .text
        )["note_id"]

        # outlinks of Source → Target
        result = json.loads(
            (await client.call_tool("get_note_links", {"note_id": source_id})).content[0].text
        )
        assert result["outlinks"] == [
            {
                "note_id": target_id,
                "title": "Target",
                "folder": "",
                "workspace": "test-ws",
                "tags": None,
                "updated_at": None,
            }
        ]
        assert result["backlinks"] == []

        # backlinks of Target → Source
        result = json.loads(
            (await client.call_tool("get_note_links", {"note_id": target_id})).content[0].text
        )
        assert result["backlinks"] == [
            {
                "note_id": source_id,
                "title": "Source",
                "folder": "",
                "workspace": "test-ws",
                "tags": None,
                "updated_at": None,
            }
        ]
        assert result["outlinks"] == []


async def test_get_note_links_not_found(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        with pytest.raises(ToolError):
            await client.call_tool("get_note_links", {"note_id": "nonexistent"})


async def test_get_note_links_include_meta(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        target_id = json.loads(
            (
                await client.call_tool(
                    "save_note", {"title": "Tagged", "content": "content", "tags": ["work"]}
                )
            )
            .content[0]
            .text
        )["note_id"]
        source_id = json.loads(
            (await client.call_tool("save_note", {"title": "Linker", "content": "[[Tagged]]"}))
            .content[0]
            .text
        )["note_id"]

        result = json.loads(
            (await client.call_tool("get_note_links", {"note_id": source_id, "include_meta": True}))
            .content[0]
            .text
        )
        entry = result["outlinks"][0]
        assert entry["note_id"] == target_id
        assert "tags" in entry
        assert entry["tags"] == ["work"]
        assert "updated_at" in entry


async def test_get_note_links_exclude_cross_workspace(workspaces_dir, mcp_server):
    """include_cross_workspace=False parameter on MCP tool hides cross-workspace backlinks."""
    for ws_name in ("ws-a", "ws-b"):
        ws_path = workspaces_dir / ws_name
        ws_path.mkdir()
        GitRepository.init(str(ws_path))
        mcp_server.workspace_repo.grant_access("u1", ws_name)

    mcp, _ = mcp_server
    async with Client(mcp) as client:
        # Create target note in ws-b.
        await client.call_tool("activate_workspace", {"name": "ws-b"})
        target_id = json.loads(
            (await client.call_tool("save_note", {"title": "Target", "content": "content"}))
            .content[0]
            .text
        )["note_id"]

        # Create source note in ws-a with a cross-workspace link to target.
        await client.call_tool("activate_workspace", {"name": "ws-a"})
        await client.call_tool(
            "save_note",
            {"title": "Source", "content": f"[[note:{target_id}]]"},
        )

        # Switch back to ws-b and verify that include_cross_workspace=False hides the backlink.
        await client.call_tool("activate_workspace", {"name": "ws-b"})
        result = json.loads(
            (
                await client.call_tool(
                    "get_note_links",
                    {"note_id": target_id, "include_cross_workspace": False},
                )
            )
            .content[0]
            .text
        )

    assert result["backlinks"] == []
