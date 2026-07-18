"""add_tag/remove_tag/set_tags happy-path tool coverage."""

import json

from fastmcp import Client


async def test_tag_tools_add_remove_set(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save = await client.call_tool(
            "save_note", {"title": "Tagi", "content": "body #inline", "tags": ["python"]}
        )
        note_id = json.loads(save.content[0].text)["note_id"]

        add = json.loads(
            (await client.call_tool("add_tag", {"note_id": note_id, "tags": ["work"]}))
            .content[0]
            .text
        )
        assert set(add["frontmatter_tags"]) == {"python", "work"}
        assert "inline" in add["tags"]  # effective includes inline #hashtag
        assert add["warnings"] == []

        rem = json.loads(
            (await client.call_tool("remove_tag", {"note_id": note_id, "tags": ["python"]}))
            .content[0]
            .text
        )
        assert rem["frontmatter_tags"] == ["work"]

        rem_inline = json.loads(
            (await client.call_tool("remove_tag", {"note_id": note_id, "tags": ["inline"]}))
            .content[0]
            .text
        )
        assert any("inline" in w for w in rem_inline["warnings"])  # inline-only -> warning

        note = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )
        st = json.loads(
            (
                await client.call_tool(
                    "set_tags",
                    {"note_id": note_id, "tags": ["docs"], "expected_sha": note["sha"]},
                )
            )
            .content[0]
            .text
        )
        assert st["frontmatter_tags"] == ["docs"]
