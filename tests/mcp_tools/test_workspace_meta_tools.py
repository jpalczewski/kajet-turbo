import json

from fastmcp import Client
from fastmcp.exceptions import ToolError


async def test_list_workspaces_returns_objects(authed_workspaces_dir, authed_mcp_server):
    mcp = authed_mcp_server.server
    async with Client(mcp) as client:
        result = await client.call_tool("list_workspaces")
    data = json.loads(result.content[0].text)
    assert {"name": "test-ws", "description": "", "folder": "", "tags": []} in data[
        "workspaces"
    ]


async def test_update_workspace_sets_description_and_tags(authed_workspaces_dir, authed_mcp_server):
    mcp = authed_mcp_server.server
    async with Client(mcp) as client:
        await client.call_tool(
            "update_workspace",
            {"name": "test-ws", "description": "Do researchu", "tags": ["#Praca"]},
        )
        listed = json.loads((await client.call_tool("list_workspaces")).content[0].text)
    ws = next(w for w in listed["workspaces"] if w["name"] == "test-ws")
    assert ws["description"] == "Do researchu" and ws["tags"] == ["praca"]


async def test_update_workspace_no_access(authed_workspaces_dir, authed_mcp_server):
    mcp = authed_mcp_server.server
    async with Client(mcp) as client:
        try:
            await client.call_tool("update_workspace", {"name": "nope", "description": "x"})
        except ToolError as e:
            data = json.loads(str(e))
            assert "brak dostępu" in data["error"]
            assert data["available"] == ["test-ws"]
        else:  # pragma: no cover
            raise AssertionError("Expected ToolError")


async def test_update_workspace_rejects_bad_tag(authed_workspaces_dir, authed_mcp_server):
    mcp = authed_mcp_server.server
    async with Client(mcp) as client:
        try:
            await client.call_tool("update_workspace", {"name": "test-ws", "tags": ["bad tag"]})
        except ToolError as e:
            assert "Invalid tag" in str(e)
        else:  # pragma: no cover
            raise AssertionError("Expected ToolError")


async def test_create_workspace_description_appears_in_list(
    authed_workspaces_dir, authed_mcp_server
):
    mcp = authed_mcp_server.server
    async with Client(mcp) as client:
        await client.call_tool(
            "create_workspace",
            {"name": "docs-ws", "description": "Dokumentacja projektowa"},
        )
        listed = json.loads((await client.call_tool("list_workspaces")).content[0].text)
    ws = next((w for w in listed["workspaces"] if w["name"] == "docs-ws"), None)
    assert ws is not None
    assert ws["description"] == "Dokumentacja projektowa"
