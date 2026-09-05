"""save_notes/edit_notes/delete_notes batch tool coverage."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from kajet_turbo.repositories.git import GitRepository
from tests.mcp_tools.helpers import call_json, seed_note, workspace_head_sha


def _second_workspace(workspaces_dir, mcp_server, name: str = "second-ws"):
    """Grants u1 a second, independent git workspace alongside test-ws."""
    ws_dir = workspaces_dir / name
    ws_dir.mkdir()
    GitRepository.init(str(ws_dir))
    mcp_server.workspace_repo.grant_access("u1", name)
    return ws_dir


async def test_save_notes_tool_batch(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        result = await client.call_tool(
            "save_notes",
            {
                "workspace": "test-ws",
                "notes": [
                    {"title": "Batch M1", "content": "a"},
                    {"title": "Batch M2", "content": "b", "tags": ["x"]},
                ],
            },
        )
    out = json.loads(result.content[0].text)
    assert [r["index"] for r in out] == [0, 1]
    assert all("note_id" in r for r in out)


async def test_edit_notes_batch_applies_together(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        saved = await client.call_tool(
            "save_note", {"workspace": "test-ws", "title": "First", "content": "one\n"}
        )
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
        r1 = await client.call_tool(
            "save_note", {"workspace": "test-ws", "title": "First", "content": "one\n"}
        )
        r2 = await client.call_tool(
            "save_note", {"workspace": "test-ws", "title": "Second", "content": "two\n"}
        )
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
        first = await seed_note(client, workspace="test-ws", title="First", content="one\n")
        second = await seed_note(client, workspace="test-ws", title="Second", content="two\n")
        id1, sha1 = first["note_id"], first["sha"]
        id2, sha2 = second["note_id"], second["sha"]

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
        first = await seed_note(client, workspace="test-ws", title="First", content="one\n")
        second = await seed_note(client, workspace="test-ws", title="Second", content="two\n")
        id1, sha1 = first["note_id"], first["sha"]
        id2 = second["note_id"]

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
        note = await seed_note(client, workspace="test-ws", title="Pair", content="Hello world.")
        note_id, sha = note["note_id"], note["sha"]
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
        assert (await call_json(client, "get_note", {"note_id": note_id}))[
            "content"
        ] == "Hello earth."


async def test_edit_notes_batch_rejects_an_item_mixing_parameter_sets(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        note = await seed_note(client, workspace="test-ws", title="Strict", content="Hello world.")
        note_id, sha = note["note_id"], note["sha"]
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
        assert (await call_json(client, "get_note", {"note_id": note_id}))[
            "content"
        ] == "Hello world."


async def test_edit_notes_batch_rejects_an_unknown_key_in_an_item(workspaces_dir, mcp_server):
    """A typo inside a batch item must fail as loudly as one on the tool's own signature."""
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        note = await seed_note(client, workspace="test-ws", title="Typo", content="one\n")
        note_id, sha = note["note_id"], note["sha"]
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
        assert "more" not in (await call_json(client, "get_note", {"note_id": note_id}))["content"]


async def test_edit_notes_batch_rejects_an_item_that_changes_nothing(workspaces_dir, mcp_server):
    """Batch scope is content + tags; an item carrying neither would commit an untouched file."""
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        note = await seed_note(client, workspace="test-ws", title="Noop", content="Body stays.")
        note_id, sha = note["note_id"], note["sha"]
        result = await call_json(
            client,
            "edit_notes",
            {"edits": [{"note_id": note_id, "expected_sha": sha, "mode": "overwrite"}]},
        )
        assert result["applied"] is False
        assert "changes nothing" in result["errors"][0]["error"]
        assert (await call_json(client, "get_note", {"note_id": note_id}))[
            "content"
        ] == "Body stays."


async def test_edit_notes_mixed_workspace_batch_leaves_both_workspaces_untouched(
    workspaces_dir, mcp_server
):
    """The resolver's mixed-workspace prevalidation (#246) must reject the whole batch
    before any write — proven here at the tool boundary by comparing each workspace's
    file content and Git HEAD before and after the rejected call, not just the response."""
    second_dir = _second_workspace(workspaces_dir, mcp_server)
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        first = await seed_note(client, workspace="test-ws", title="First", content="one\n")
        second = await seed_note(client, workspace="second-ws", title="Second", content="two\n")

        head1_before = workspace_head_sha(workspaces_dir / "test-ws")
        head2_before = workspace_head_sha(second_dir)

        with pytest.raises(ToolError, match="MIXED_WORKSPACES"):
            await client.call_tool(
                "edit_notes",
                {
                    "edits": [
                        {
                            "note_id": first["note_id"],
                            "expected_sha": first["sha"],
                            "mode": "append",
                            "content": "more",
                        },
                        {
                            "note_id": second["note_id"],
                            "expected_sha": second["sha"],
                            "mode": "append",
                            "content": "more",
                        },
                    ]
                },
            )

        assert workspace_head_sha(workspaces_dir / "test-ws") == head1_before
        assert workspace_head_sha(second_dir) == head2_before
        assert (await call_json(client, "get_note", {"note_id": first["note_id"]}))[
            "content"
        ] == "one"
        assert (await call_json(client, "get_note", {"note_id": second["note_id"]}))[
            "content"
        ] == "two"


async def test_delete_notes_mixed_workspace_batch_leaves_both_workspaces_untouched(
    workspaces_dir, mcp_server
):
    """Same guarantee as the edit_notes case above, for delete_notes: a batch spanning
    two workspaces is rejected before either workspace's files or Git HEAD are touched."""
    second_dir = _second_workspace(workspaces_dir, mcp_server)
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        first = await seed_note(client, workspace="test-ws", title="First", content="one\n")
        second = await seed_note(client, workspace="second-ws", title="Second", content="two\n")

        head1_before = workspace_head_sha(workspaces_dir / "test-ws")
        head2_before = workspace_head_sha(second_dir)

        with pytest.raises(ToolError, match="MIXED_WORKSPACES"):
            await client.call_tool(
                "delete_notes",
                {
                    "deletes": [
                        {"note_id": first["note_id"], "expected_sha": first["sha"]},
                        {"note_id": second["note_id"], "expected_sha": second["sha"]},
                    ]
                },
            )

        assert workspace_head_sha(workspaces_dir / "test-ws") == head1_before
        assert workspace_head_sha(second_dir) == head2_before
        assert (await call_json(client, "get_note", {"note_id": first["note_id"]}))[
            "content"
        ] == "one"
        assert (await call_json(client, "get_note", {"note_id": second["note_id"]}))[
            "content"
        ] == "two"
