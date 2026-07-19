"""expected_sha gating for set_tags/edit_note/delete_note/restore_note_version + sha-leak checks."""

import json

from fastmcp import Client

from tests.mcp_tools.helpers import SHA_LIKE


async def _save_and_get_sha(client, title: str, content: str, tags: list[str]) -> tuple[str, str]:
    saved = await client.call_tool("save_note", {"title": title, "content": content, "tags": tags})
    note_id = json.loads(saved.content[0].text)["note_id"]
    note = json.loads((await client.call_tool("get_note", {"note_id": note_id})).content[0].text)
    return note_id, note["sha"]


async def test_set_tags_applies_with_fresh_sha(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        note_id, sha = await _save_and_get_sha(client, "Tagged", "body", ["docs", "extra"])
        res = json.loads(
            (
                await client.call_tool(
                    "set_tags", {"note_id": note_id, "tags": ["docs"], "expected_sha": sha}
                )
            )
            .content[0]
            .text
        )
        assert res["frontmatter_tags"] == ["docs"]


async def test_set_tags_stale_sha_returns_stale_version(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        note_id, _sha = await _save_and_get_sha(client, "Tagged2", "body", ["docs", "extra"])
        res = json.loads(
            (
                await client.call_tool(
                    "set_tags",
                    {"note_id": note_id, "tags": ["docs"], "expected_sha": "0" * 12},
                )
            )
            .content[0]
            .text
        )
        assert "nieaktualny" in res["error"]
        assert not SHA_LIKE.search(res["error"])  # never leak the current sha
        # nothing changed
        note = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )
        assert sorted(note["tags"]) == ["docs", "extra"]


async def test_edit_note_overwrite_applies_with_fresh_sha(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        note_id = json.loads(
            (await client.call_tool("save_note", {"title": "E", "content": "stara"}))
            .content[0]
            .text
        )["note_id"]
        sha = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )["sha"]
        res = json.loads(
            (
                await client.call_tool(
                    "edit_note", {"note_id": note_id, "expected_sha": sha, "content": "nowa"}
                )
            )
            .content[0]
            .text
        )
        assert res["note_id"] == note_id
        note = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )
        assert note["content"] == "nowa"


async def test_edit_note_stale_sha_rejected(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        saved = await client.call_tool("save_note", {"title": "Stale", "content": "v1\n"})
        note_id = json.loads(saved.content[0].text)["note_id"]
        stale_sha = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )["sha"]
        await client.call_tool(
            "edit_note",
            {"note_id": note_id, "expected_sha": stale_sha, "mode": "append", "content": "v2"},
        )

        result = await client.call_tool(
            "edit_note",
            {"note_id": note_id, "expected_sha": stale_sha, "mode": "append", "content": "v3"},
        )

        data = json.loads(result.content[0].text)
        assert "current_sha" not in data
        note = await client.call_tool("get_note", {"note_id": note_id})
        assert "v3" not in note.content[0].text


async def test_edit_notes_batch_stale_sha_rejects_all(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        r1 = await client.call_tool("save_note", {"title": "First", "content": "one\n"})
        r2 = await client.call_tool("save_note", {"title": "Second", "content": "two\n"})
        id1 = json.loads(r1.content[0].text)["note_id"]
        id2 = json.loads(r2.content[0].text)["note_id"]
        sha2 = json.loads((await client.call_tool("get_note", {"note_id": id2})).content[0].text)[
            "sha"
        ]

        result = await client.call_tool(
            "edit_notes",
            {
                "edits": [
                    {
                        "note_id": id1,
                        "expected_sha": "0" * 40,
                        "mode": "append",
                        "content": "more",
                    },
                    {
                        "note_id": id2,
                        "expected_sha": sha2,
                        "mode": "append",
                        "content": "more",
                    },
                ]
            },
        )
        data = json.loads(result.content[0].text)
        assert data["applied"] is False
        note1 = await client.call_tool("get_note", {"note_id": id1})
        note2 = await client.call_tool("get_note", {"note_id": id2})
        assert "more" not in note1.content[0].text
        assert "more" not in note2.content[0].text


async def test_delete_note_stale_sha_returns_stale_version(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool("save_note", {"title": "Zostaje", "content": "treść"})
        note_id = json.loads(save_result.content[0].text)["note_id"]
        res = json.loads(
            (await client.call_tool("delete_note", {"note_id": note_id, "expected_sha": "0" * 12}))
            .content[0]
            .text
        )
        assert "nieaktualny" in res["error"]
        assert not SHA_LIKE.search(res["error"])
        # still there
        await client.call_tool("get_note", {"note_id": note_id})


async def test_restore_note_version_stale_sha_returns_stale_version(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        saved = await client.call_tool("save_note", {"title": "Hist", "content": "v1"})
        note_id = json.loads(saved.content[0].text)["note_id"]
        sha1 = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )["sha"]
        await client.call_tool(
            "edit_note", {"note_id": note_id, "expected_sha": sha1, "content": "v2"}
        )
        res = json.loads(
            (
                await client.call_tool(
                    "restore_note_version",
                    {"note_id": note_id, "sha": sha1, "expected_sha": "0" * 12},
                )
            )
            .content[0]
            .text
        )
        assert "nieaktualny" in res["error"]
        # content unchanged
        note = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )
        assert note["content"] == "v2"


async def test_stale_sha_responses_never_leak_a_sha(workspaces_dir, mcp_server):
    # Regression guard: a leaked sha (under any field name) lets an agent skip get_note
    # entirely — read the error, retry with the leaked value, never see the content.
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        r1 = await client.call_tool("save_note", {"title": "First", "content": "one\n"})
        r2 = await client.call_tool("save_note", {"title": "Second", "content": "two\n"})
        id1 = json.loads(r1.content[0].text)["note_id"]
        id2 = json.loads(r2.content[0].text)["note_id"]
        sha2 = json.loads((await client.call_tool("get_note", {"note_id": id2})).content[0].text)[
            "sha"
        ]

        edit_result = await client.call_tool(
            "edit_note",
            {"note_id": id1, "expected_sha": "0" * 40, "mode": "append", "content": "x"},
        )
        edit_notes_result = await client.call_tool(
            "edit_notes",
            {
                "edits": [
                    {"note_id": id1, "expected_sha": "0" * 40, "mode": "append", "content": "x"},
                    {"note_id": id2, "expected_sha": sha2, "mode": "append", "content": "x"},
                ]
            },
        )
        delete_result = await client.call_tool(
            "delete_notes",
            {"deletes": [{"note_id": id1, "expected_sha": "0" * 40}]},
        )

        for result in (edit_result, edit_notes_result, delete_result):
            assert not SHA_LIKE.search(result.content[0].text)
