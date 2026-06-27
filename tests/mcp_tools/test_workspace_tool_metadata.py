from fastmcp import Client


async def test_workspace_tools_have_safety_annotations(mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    assert tools["list_workspaces"].annotations.readOnlyHint is True
    assert tools["list_workspaces"].annotations.idempotentHint is True
    assert tools["list_workspaces"].annotations.destructiveHint is False
    assert tools["list_workspace_settings"].annotations.readOnlyHint is True

    for name in [
        "activate_workspace",
        "create_workspace",
        "update_workspace",
        "set_workspace_setting",
    ]:
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.destructiveHint is False
        assert tools[name].annotations.openWorldHint is False

    assert tools["activate_workspace"].annotations.idempotentHint is True
    assert tools["create_workspace"].annotations.idempotentHint is False
    assert tools["update_workspace"].annotations.idempotentHint is True
    assert tools["set_workspace_setting"].annotations.idempotentHint is True


async def test_workspace_tools_can_be_filtered_by_read_tag(mcp_server):
    mcp, _ = mcp_server
    mcp.enable(tags={"read"}, only=True)

    async with Client(mcp) as client:
        tool_names = {tool.name for tool in await client.list_tools()}

    assert {"list_workspaces", "list_workspace_settings"} <= tool_names
    assert "activate_workspace" not in tool_names
    assert "create_workspace" not in tool_names
    assert "update_workspace" not in tool_names
    assert "set_workspace_setting" not in tool_names
