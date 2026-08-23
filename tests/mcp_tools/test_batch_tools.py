"""save_notes/edit_notes/delete_notes batch tool coverage."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError


async def test_save_notes_tool_batch(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        result = await client.call_tool(
            "save_notes",
            {
                "notes": [
                    {"title": "Batch M1", "content": "a"},
                    {"title": "Batch M2", "content": "b", "tags": ["x"]},
                ]
            },
        )
    out = json.loads(result.content[0].text)
    assert [r["index"] for r in out] == [0, 1]
    assert all("note_id" in r for r in out)


async def test_edit_notes_batch_applies_together(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        saved = await client.call_tool("save_note", {"title": "First", "content": "one\n"})
        note_id = json.loads(saved.content[0].text)["note_id"]
        sha = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )["sha"]
        result = await client.call_tool(
            "edit_notes",
            {
                "edits": [
                    {
                        "note_id": note_id,
                        "expected_sha": sha,
                        "mode": "append",
                        "content": "more",
                    }
                ]
            },
        )
        data = json.loads(result.content[0].text)
        assert data["applied"] is True
        note = await client.call_tool("get_note", {"note_id": note_id})
        assert "more" in note.content[0].text


async def test_edit_notes_batch_rejects_all_on_one_bad_item(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        r1 = await client.call_tool("save_note", {"title": "First", "content": "one\n"})
        r2 = await client.call_tool("save_note", {"title": "Second", "content": "two\n"})
        id1 = json.loads(r1.content[0].text)["note_id"]
        id2 = json.loads(r2.content[0].text)["note_id"]
        sha1 = json.loads((await client.call_tool("get_note", {"note_id": id1})).content[0].text)[
            "sha"
        ]
        sha2 = json.loads((await client.call_tool("get_note", {"note_id": id2})).content[0].text)[
            "sha"
        ]
        result = await client.call_tool(
            "edit_notes",
            {
                "edits": [
                    {
                        "note_id": id1,
                        "expected_sha": sha1,
                        "mode": "append",
                        "content": "more",
                    },
                    {
                        "note_id": id2,
                        "expected_sha": sha2,
                        "mode": "replace_text",
                        "old_str": "does-not-exist",
                        "new_str": "x",
                    },
                ]
            },
        )
        data = json.loads(result.content[0].text)
        assert data["applied"] is False
        note1 = await client.call_tool("get_note", {"note_id": id1})
        assert "more" not in note1.content[0].text


async def test_delete_notes_batch_applies_together(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        r1 = await client.call_tool("save_note", {"title": "First", "content": "one\n"})
        r2 = await client.call_tool("save_note", {"title": "Second", "content": "two\n"})
        id1 = json.loads(r1.content[0].text)["note_id"]
        id2 = json.loads(r2.content[0].text)["note_id"]
        h1 = await client.call_tool("get_note_history", {"note_id": id1})
        h2 = await client.call_tool("get_note_history", {"note_id": id2})
        sha1 = json.loads(h1.content[0].text)[0]["sha"]
        sha2 = json.loads(h2.content[0].text)[0]["sha"]

        result = await client.call_tool(
            "delete_notes",
            {
                "deletes": [
                    {"note_id": id1, "expected_sha": sha1},
                    {"note_id": id2, "expected_sha": sha2},
                ]
            },
        )
        data = json.loads(result.content[0].text)
        assert data["applied"] is True
        with pytest.raises(ToolError):
            await client.call_tool("get_note", {"note_id": id1})
        with pytest.raises(ToolError):
            await client.call_tool("get_note", {"note_id": id2})


async def test_delete_notes_batch_rejects_all_on_stale_sha(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        r1 = await client.call_tool("save_note", {"title": "First", "content": "one\n"})
        r2 = await client.call_tool("save_note", {"title": "Second", "content": "two\n"})
        id1 = json.loads(r1.content[0].text)["note_id"]
        id2 = json.loads(r2.content[0].text)["note_id"]
        h1 = await client.call_tool("get_note_history", {"note_id": id1})
        sha1 = json.loads(h1.content[0].text)[0]["sha"]

        result = await client.call_tool(
            "delete_notes",
            {
                "deletes": [
                    {"note_id": id1, "expected_sha": sha1},
                    {"note_id": id2, "expected_sha": "0" * 40},
                ]
            },
        )
        data = json.loads(result.content[0].text)
        assert data["applied"] is False
        assert "current_sha" not in data["errors"][0]
        # nothing deleted, including the valid first item
        get1 = await client.call_tool("get_note", {"note_id": id1})
        assert "First" in get1.content[0].text


async def test_edit_notes_batch_takes_old_str_and_new_str_per_item(workspaces_dir, mcp_server):
    """NoteEditInput carries the same parameter split as edit_note."""
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        saved = await client.call_tool("save_note", {"title": "Pair", "content": "Hello world."})
        note_id = json.loads(saved.content[0].text)["note_id"]
        sha = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )["sha"]
        result = await client.call_tool(
            "edit_notes",
            {
                "edits": [
                    {
                        "note_id": note_id,
                        "expected_sha": sha,
                        "mode": "replace_text",
                        "old_str": "world",
                        "new_str": "earth",
                    }
                ]
            },
        )
        assert json.loads(result.content[0].text)["applied"] is True
        note = await client.call_tool("get_note", {"note_id": note_id})
        assert "Hello earth." in note.content[0].text


async def test_edit_notes_batch_rejects_an_item_mixing_parameter_sets(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        saved = await client.call_tool("save_note", {"title": "Strict", "content": "Hello world."})
        note_id = json.loads(saved.content[0].text)["note_id"]
        sha = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )["sha"]
        result = await client.call_tool(
            "edit_notes",
            {
                "edits": [
                    {
                        "note_id": note_id,
                        "expected_sha": sha,
                        "mode": "replace_text",
                        "old_str": "world",
                        "content": "earth",
                    }
                ]
            },
        )
        data = json.loads(result.content[0].text)
        assert data["applied"] is False
        assert "does not take content" in data["errors"][0]["error"]
        note = await client.call_tool("get_note", {"note_id": note_id})
        assert "Hello world." in note.content[0].text


async def test_edit_notes_batch_rejects_an_unknown_key_in_an_item(workspaces_dir, mcp_server):
    """A typo inside a batch item must fail as loudly as one on the tool's own signature."""
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        saved = await client.call_tool("save_note", {"title": "Typo", "content": "one\n"})
        note_id = json.loads(saved.content[0].text)["note_id"]
        sha = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )["sha"]
        with pytest.raises(ToolError, match="old_text"):
            await client.call_tool(
                "edit_notes",
                {
                    "edits": [
                        {
                            "note_id": note_id,
                            "expected_sha": sha,
                            "mode": "append",
                            "content": "more",
                            "old_text": "junk",
                        }
                    ]
                },
            )
        note = await client.call_tool("get_note", {"note_id": note_id})
        assert "more" not in note.content[0].text
