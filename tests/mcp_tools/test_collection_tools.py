"""define_collection/delete_collection/list_collections boundary coverage.

One case per tool proves the ValueError -> ToolError wiring and FastMCP plumbing;
the collision/redefinition/deletion policy matrix lives in tests/test_collections.py
and tests/services/test_collections.py, the cheaper layers per tests/CLAUDE.md.
"""

from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.mcp_tools.helpers import call_json


async def test_define_collection_add_and_redefine(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})

        added = await call_json(
            client,
            "define_collection",
            {
                "name": "weekly",
                "grain": "week",
                "cardinality": "one",
                "folder": "weekly/{year}",
                "title": "{key}",
            },
        )
        assert added["verb"] == "add"
        assert added["affected_count"] == 0
        assert added["collection"]["folder"] == "weekly/{year}"

        redefined = await call_json(
            client,
            "define_collection",
            {
                "name": "weekly",
                "grain": "week",
                "cardinality": "one",
                "folder": "weekly-v2/{year}",
                "title": "{key}",
            },
        )
        assert redefined["verb"] == "update"
        assert redefined["affected_count"] == 0


async def test_define_collection_collision_rejected(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await call_json(
            client,
            "define_collection",
            {
                "name": "weekly",
                "grain": "week",
                "cardinality": "one",
                "folder": "archive/{year}",
                "title": "{key}",
            },
        )

        try:
            await client.call_tool(
                "define_collection",
                {
                    "name": "yearly",
                    "grain": "year",
                    "cardinality": "one",
                    "folder": "archive/{year}",
                    "title": "{key}",
                },
            )
            raise AssertionError("expected ToolError")
        except ToolError as exc:
            assert "weekly" in str(exc)
            assert "yearly" in str(exc)


async def test_delete_and_list_collections(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await call_json(
            client,
            "define_collection",
            {
                "name": "weekly",
                "grain": "week",
                "cardinality": "one",
                "folder": "weekly/{year}",
                "title": "{key}",
            },
        )

        listed = await call_json(client, "list_collections")
        assert [c["name"] for c in listed] == ["weekly"]

        deleted = await call_json(client, "delete_collection", {"name": "weekly"})
        assert deleted == {"name": "weekly", "deleted": True}
        # An empty list result carries no text content block, so check .data directly
        # rather than the call_json helper (which indexes content[0]).
        after_delete = await client.call_tool("list_collections")
        assert after_delete.data == []


async def test_delete_unknown_collection_rejected(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        try:
            await client.call_tool("delete_collection", {"name": "nope"})
            raise AssertionError("expected ToolError")
        except ToolError as exc:
            assert "nope" in str(exc)
