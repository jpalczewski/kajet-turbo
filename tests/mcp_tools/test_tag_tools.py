"""add_tag/remove_tag/set_tags/rename_tag happy-path tool coverage."""

import json
from types import SimpleNamespace

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.mcp_tools.helpers import call_json


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


async def test_rename_tag_moves_subtree_and_inline_hashtags(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await call_json(
            client, "save_note", {"title": "A", "content": "body", "tags": ["work/projects"]}
        )
        inline = await call_json(
            client, "save_note", {"title": "B", "content": "patrz #work tutaj", "tags": []}
        )
        await call_json(
            client, "save_note", {"title": "C", "content": "body", "tags": ["workflow"]}
        )

        result = await call_json(client, "rename_tag", {"old": "work", "new": "job"})
        assert (result["renamed"], result["merged"], result["inline_rewritten"]) == (2, False, 1)

        tags = {t["path"] for t in await call_json(client, "list_tags", {})}
        assert tags == {"job", "job/projects", "workflow"}
        note = await call_json(client, "get_note", {"note_id": inline["note_id"]})
        assert "#job" in note["content"]


async def test_rename_tag_reports_a_conflict_unless_merge_is_requested(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await call_json(client, "save_note", {"title": "A", "content": "body", "tags": ["osoba"]})
        await call_json(client, "save_note", {"title": "B", "content": "body", "tags": ["osoby"]})

        conflict = await call_json(client, "rename_tag", {"old": "osoba", "new": "osoby"})
        assert conflict["target"] == "osoby"
        assert {t["path"] for t in await call_json(client, "list_tags", {})} == {"osoba", "osoby"}

        merged = await call_json(
            client, "rename_tag", {"old": "osoba", "new": "osoby", "merge": True}
        )
        assert merged["merged"] is True
        assert {t["path"] for t in await call_json(client, "list_tags", {})} == {"osoby"}


async def test_rename_tag_rejects_an_unknown_tag(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await call_json(client, "save_note", {"title": "A", "content": "body", "tags": ["work"]})
        with pytest.raises(ToolError, match="nie istnieje"):
            await client.call_tool("rename_tag", {"old": "wrok", "new": "job"})


async def test_per_note_tag_tools_publish_note_updated_only_on_a_real_change(
    workspaces_dir, mcp_server, monkeypatch
):
    mcp, _ = mcp_server
    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "kajet_turbo.mcp.tooling.event_repo",
        SimpleNamespace(publish=lambda owner_id, kind, payload: published.append((kind, payload))),
    )
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        saved = await call_json(client, "save_note", {"title": "A", "content": "body"})
        published.clear()

        await call_json(client, "add_tag", {"note_id": saved["note_id"], "tags": ["work"]})
        assert [kind for kind, _ in published] == ["note_updated"]
        assert published[0][1]["note_id"] == saved["note_id"]

        # Idempotent repeat writes nothing, so it must not wake every connected client.
        published.clear()
        await call_json(client, "add_tag", {"note_id": saved["note_id"], "tags": ["work"]})
        assert published == []
