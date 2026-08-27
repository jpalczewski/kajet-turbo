import json

from fastmcp import Client
from fastmcp.exceptions import ToolError


async def test_list_workspace_settings_shape(workspaces_dir, mcp_server):
    mcp = mcp_server.server
    async with Client(mcp) as client:
        result = await client.call_tool("list_workspace_settings", {"name": "test-ws"})
    data = json.loads(result.content[0].text)
    assert "settings" in data
    keys = {s["key"] for s in data["settings"]}
    assert {"include_in_search_all", "validate_links"} <= keys
    search_all = next(s for s in data["settings"] if s["key"] == "include_in_search_all")
    assert search_all["value"] is True
    assert search_all["default"] is True
    vl = next(s for s in data["settings"] if s["key"] == "validate_links")
    assert vl["value"] is True
    assert vl["default"] is True
    assert vl["type"] == "bool"


async def test_list_workspace_settings_no_access(workspaces_dir, mcp_server):
    mcp = mcp_server.server
    async with Client(mcp) as client:
        try:
            await client.call_tool("list_workspace_settings", {"name": "no-such-ws"})
        except ToolError as e:
            data = json.loads(str(e))
            assert "brak dostępu" in data["error"]
            assert data["available"] == ["test-ws"]
        else:  # pragma: no cover
            raise AssertionError("Expected ToolError")


async def test_set_workspace_setting_flips_value(workspaces_dir, mcp_server):
    mcp = mcp_server.server
    async with Client(mcp) as client:
        result = await client.call_tool(
            "set_workspace_setting",
            {"name": "test-ws", "setting": "validate_links", "value": False},
        )
    data = json.loads(result.content[0].text)
    assert data["setting"] == "validate_links"
    assert data["value"] is False
    assert "message" in data


async def test_set_workspace_setting_persists(workspaces_dir, mcp_server):
    mcp = mcp_server.server
    async with Client(mcp) as client:
        await client.call_tool(
            "set_workspace_setting",
            {"name": "test-ws", "setting": "validate_links", "value": False},
        )
        listed = json.loads(
            (await client.call_tool("list_workspace_settings", {"name": "test-ws"})).content[0].text
        )
    vl = next(s for s in listed["settings"] if s["key"] == "validate_links")
    assert vl["value"] is False


async def test_set_workspace_setting_no_access(workspaces_dir, mcp_server):
    mcp = mcp_server.server
    async with Client(mcp) as client:
        try:
            await client.call_tool(
                "set_workspace_setting",
                {"name": "no-such-ws", "setting": "validate_links", "value": False},
            )
        except ToolError as e:
            data = json.loads(str(e))
            assert "brak dostępu" in data["error"]
            assert data["available"] == ["test-ws"]
        else:  # pragma: no cover
            raise AssertionError("Expected ToolError")
