from fastmcp import Client

READ_TOOLS = {
    "list_workspaces",
    "list_workspace_settings",
    "get_note",
    "list_notes",
    "search_notes",
    "list_folders",
    "list_tags",
    "get_note_history",
    "get_note_at_version",
    "get_note_links",
}

WRITE_TOOLS = {
    "activate_workspace",
    "create_workspace",
    "update_workspace",
    "set_workspace_setting",
    "save_note",
    "save_notes",
    "edit_note",
    "move_note",
    "delete_note",
    "reindex_workspace",
    "move_folder",
    "rename_folder",
    "prune_empty_folders",
    "add_tag",
    "remove_tag",
    "set_tags",
    "restore_note_version",
}

DESTRUCTIVE_TOOLS = {
    "edit_note",
    "delete_note",
    "set_tags",
    "restore_note_version",
}


async def test_mcp_tools_have_safety_annotations(mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    for name in READ_TOOLS:
        assert tools[name].annotations.readOnlyHint is True
        assert tools[name].annotations.idempotentHint is True
        assert tools[name].annotations.destructiveHint is False
        assert tools[name].annotations.openWorldHint is False

    for name in WRITE_TOOLS:
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.openWorldHint is False
        assert tools[name].annotations.destructiveHint is (name in DESTRUCTIVE_TOOLS)

    assert tools["activate_workspace"].annotations.idempotentHint is True
    assert tools["create_workspace"].annotations.idempotentHint is False
    assert tools["update_workspace"].annotations.idempotentHint is True
    assert tools["set_workspace_setting"].annotations.idempotentHint is True
    assert tools["reindex_workspace"].annotations.idempotentHint is True
    assert tools["add_tag"].annotations.idempotentHint is True
    assert tools["remove_tag"].annotations.idempotentHint is True
    assert tools["prune_empty_folders"].annotations.idempotentHint is True


async def test_context_dependencies_are_hidden_from_tool_schemas(mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    for name in ["get_note", "edit_note", "set_tags", "list_workspace_settings"]:
        schema = tools[name].inputSchema
        properties = schema.get("properties", {})
        assert "ws" not in properties
        assert "ctx" not in properties


async def test_mcp_tools_can_be_filtered_by_read_tag(mcp_server):
    mcp, _ = mcp_server
    mcp.enable(tags={"read"}, only=True)

    async with Client(mcp) as client:
        tool_names = {tool.name for tool in await client.list_tools()}

    assert tool_names >= READ_TOOLS
    assert not (WRITE_TOOLS & tool_names)
