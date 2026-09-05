"""get_note_links tool coverage."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from kajet_turbo.repositories.git import GitRepository


async def test_get_note_links(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        target_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {"workspace": "test-ws", "title": "Target", "content": "content"},
                )
            )
            .content[0]
            .text
        )["note_id"]
        source_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {
                        "workspace": "test-ws",
                        "title": "Source",
                        "content": "see [[Target]]",
                    },
                )
            )
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
        with pytest.raises(ToolError):
            await client.call_tool("get_note_links", {"note_id": "nonexistent"})


async def test_get_note_links_include_meta(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        target_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {
                        "workspace": "test-ws",
                        "title": "Tagged",
                        "content": "content",
                        "tags": ["work"],
                    },
                )
            )
            .content[0]
            .text
        )["note_id"]
        source_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {"workspace": "test-ws", "title": "Linker", "content": "[[Tagged]]"},
                )
            )
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
        target_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {"workspace": "ws-b", "title": "Target", "content": "content"},
                )
            )
            .content[0]
            .text
        )["note_id"]

        # Create source note in ws-a with a cross-workspace link to target.
        await client.call_tool(
            "save_note",
            {"workspace": "ws-a", "title": "Source", "content": f"[[note:{target_id}]]"},
        )

        # Verify that include_cross_workspace=False hides the backlink.
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


async def test_get_workspace_graph(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        target_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {"workspace": "test-ws", "title": "Target", "content": "content"},
                )
            )
            .content[0]
            .text
        )["note_id"]
        source_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {
                        "workspace": "test-ws",
                        "title": "Source",
                        "content": "see [[Target]]",
                    },
                )
            )
            .content[0]
            .text
        )["note_id"]
        json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {"workspace": "test-ws", "title": "Lonely", "content": "no links"},
                )
            )
            .content[0]
            .text
        )

        result = json.loads(
            (await client.call_tool("get_workspace_graph", {"workspace": "test-ws"}))
            .content[0]
            .text
        )

    assert {n["title"] for n in result["nodes"]} == {"Target", "Source", "Lonely"}
    assert result["edges"] == [{"source": source_id, "target": target_id}]
    assert result["dangling_links"] is None


async def test_get_note_neighborhood(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        target_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {"workspace": "test-ws", "title": "Target", "content": "content"},
                )
            )
            .content[0]
            .text
        )["note_id"]
        source_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {"workspace": "test-ws", "title": "Source", "content": "[[Target]]"},
                )
            )
            .content[0]
            .text
        )["note_id"]
        result = json.loads(
            (await client.call_tool("get_note_neighborhood", {"note_id": source_id}))
            .content[0]
            .text
        )

    assert result["edges"] == [{"source": source_id, "target": target_id}]


async def test_get_workspace_graph_includes_tag_hubs(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        source_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {
                        "workspace": "test-ws",
                        "title": "Source",
                        "content": "",
                        "tags": ["work/projects"],
                    },
                )
            )
            .content[0]
            .text
        )["note_id"]
        result = json.loads(
            (
                await client.call_tool(
                    "get_workspace_graph", {"workspace": "test-ws", "include_tags": True}
                )
            )
            .content[0]
            .text
        )

    tags = {node["path"]: node for node in result["nodes"] if node["kind"] == "tag"}
    assert {"work", "work/projects"} == set(tags)
    assert {"source": source_id, "target": tags["work/projects"]["id"]} in result["edges"]
