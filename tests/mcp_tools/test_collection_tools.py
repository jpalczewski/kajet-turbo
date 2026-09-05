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
        added = await call_json(
            client,
            "define_collection",
            {
                "workspace": "test-ws",
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
                "workspace": "test-ws",
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
        await call_json(
            client,
            "define_collection",
            {
                "workspace": "test-ws",
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
                    "workspace": "test-ws",
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
        await call_json(
            client,
            "define_collection",
            {
                "workspace": "test-ws",
                "name": "weekly",
                "grain": "week",
                "cardinality": "one",
                "folder": "weekly/{year}",
                "title": "{key}",
            },
        )

        listed = await call_json(client, "list_collections", {"workspace": "test-ws"})
        assert [c["name"] for c in listed] == ["weekly"]

        deleted = await call_json(
            client, "delete_collection", {"name": "weekly", "workspace": "test-ws"}
        )
        assert deleted == {"name": "weekly", "deleted": True}
        # An empty list result carries no text content block, so check .data directly
        # rather than the call_json helper (which indexes content[0]).
        after_delete = await client.call_tool("list_collections", {"workspace": "test-ws"})
        assert after_delete.data == []


async def test_delete_unknown_collection_rejected(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        try:
            await client.call_tool("delete_collection", {"name": "nope", "workspace": "test-ws"})
            raise AssertionError("expected ToolError")
        except ToolError as exc:
            assert "nope" in str(exc)


async def test_open_entry_creates_then_resolves(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await call_json(
            client,
            "define_collection",
            {
                "workspace": "test-ws",
                "name": "journal",
                "grain": "day",
                "cardinality": "one",
                "folder": "journal/{year}/{month}",
                "title": "{date}",
            },
        )

        created = await call_json(
            client,
            "open_entry",
            {"collection": "journal", "date": "2026-06-15", "workspace": "test-ws"},
        )
        assert created["created"] is True
        assert created["folder"] == "journal/2026/06"
        assert created["title"] == "2026-06-15"
        assert created["occurred_at"] == "2026-06-15"

        resolved = await call_json(
            client,
            "open_entry",
            {"collection": "journal", "date": "2026-06-15", "workspace": "test-ws"},
        )
        assert resolved["created"] is False
        assert resolved["note_id"] == created["note_id"]


async def test_open_entry_unknown_collection_rejected(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        try:
            await client.call_tool(
                "open_entry",
                {"collection": "nope", "date": "2026-06-15", "workspace": "test-ws"},
            )
            raise AssertionError("expected ToolError")
        except ToolError as exc:
            assert "nope" in str(exc)


async def test_open_entry_rejects_malformed_date(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await call_json(
            client,
            "define_collection",
            {
                "workspace": "test-ws",
                "name": "journal",
                "grain": "day",
                "cardinality": "one",
                "folder": "journal/{year}/{month}",
                "title": "{date}",
            },
        )

        try:
            await client.call_tool(
                "open_entry",
                {"collection": "journal", "date": "15-06-2026", "workspace": "test-ws"},
            )
            raise AssertionError("expected ToolError")
        except ToolError as exc:
            assert "date" in str(exc)


async def test_list_collection_entries_returns_members_across_periods(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await call_json(
            client,
            "define_collection",
            {
                "workspace": "test-ws",
                "name": "journal",
                "grain": "day",
                "cardinality": "one",
                "folder": "journal/{year}/{month}",
                "title": "{date}",
            },
        )
        june = await call_json(
            client,
            "open_entry",
            {"collection": "journal", "date": "2026-06-15", "workspace": "test-ws"},
        )
        july = await call_json(
            client,
            "open_entry",
            {"collection": "journal", "date": "2026-07-01", "workspace": "test-ws"},
        )
        await call_json(
            client,
            "save_note",
            {
                "workspace": "test-ws",
                "title": "Not a date",
                "content": "",
                "folder": "journal/2026/06",
            },
        )

        result = await call_json(
            client, "list_collection_entries", {"collection": "journal", "workspace": "test-ws"}
        )

        assert {n["note_id"] for n in result} == {june["note_id"], july["note_id"]}


async def test_list_collection_entries_unknown_collection_rejected(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        try:
            await client.call_tool(
                "list_collection_entries", {"collection": "nope", "workspace": "test-ws"}
            )
            raise AssertionError("expected ToolError")
        except ToolError as exc:
            assert "nope" in str(exc)
