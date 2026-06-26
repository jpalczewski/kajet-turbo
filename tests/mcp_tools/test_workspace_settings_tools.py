import json

from fastmcp import Client


async def test_list_workspace_settings_shape(authed_workspaces_dir, authed_mcp_server):
    mcp = authed_mcp_server.server
    async with Client(mcp) as client:
        result = await client.call_tool("list_workspace_settings", {"name": "test-ws"})
    data = json.loads(result.content[0].text)
    assert "settings" in data
    keys = {s["key"] for s in data["settings"]}
    assert "validate_links" in keys
    vl = next(s for s in data["settings"] if s["key"] == "validate_links")
    assert vl["value"] is True
    assert vl["default"] is True
    assert vl["type"] == "bool"


async def test_list_workspace_settings_no_access(authed_workspaces_dir, authed_mcp_server):
    mcp = authed_mcp_server.server
    async with Client(mcp) as client:
        result = await client.call_tool("list_workspace_settings", {"name": "no-such-ws"})
    data = json.loads(result.content[0].text)
    assert "error" in data


async def test_set_workspace_setting_flips_value(authed_workspaces_dir, authed_mcp_server):
    mcp = authed_mcp_server.server
    async with Client(mcp) as client:
        result = await client.call_tool(
            "set_workspace_setting",
            {"name": "test-ws", "setting": "validate_links", "value": False},
        )
    data = json.loads(result.content[0].text)
    assert data["setting"] == "validate_links"
    assert data["value"] is False
    assert "message" in data


async def test_set_workspace_setting_persists(authed_workspaces_dir, authed_mcp_server):
    mcp = authed_mcp_server.server
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


async def test_set_workspace_setting_no_access(authed_workspaces_dir, authed_mcp_server):
    mcp = authed_mcp_server.server
    async with Client(mcp) as client:
        result = await client.call_tool(
            "set_workspace_setting",
            {"name": "no-such-ws", "setting": "validate_links", "value": False},
        )
    data = json.loads(result.content[0].text)
    assert "error" in data
