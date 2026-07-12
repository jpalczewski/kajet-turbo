"""set_tags/edit_note confirmation gates and stale expected_sha / sha-leak regressions."""

import json

from fastmcp import Client

from tests.mcp_tools.helpers import SHA_LIKE


async def test_set_tags_gate_fallback_without_elicitation(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:  # no elicitation_handler -> no capability
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        note_id = json.loads(
            (
                await client.call_tool(
                    "save_note", {"title": "T", "content": "x", "tags": ["python", "work"]}
                )
            )
            .content[0]
            .text
        )["note_id"]
        res = json.loads(
            (await client.call_tool("set_tags", {"note_id": note_id, "tags": ["docs"]}))
            .content[0]
            .text
        )
        assert res["requires_confirmation"] is True
        assert set(res["would_remove_tags"]) == {"python", "work"}


async def test_set_tags_gate_confirm_flag_applies(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        note_id = json.loads(
            (
                await client.call_tool(
                    "save_note", {"title": "T2", "content": "x", "tags": ["python", "work"]}
                )
            )
            .content[0]
            .text
        )["note_id"]
        res = json.loads(
            (
                await client.call_tool(
                    "set_tags", {"note_id": note_id, "tags": ["docs"], "confirm": True}
                )
            )
            .content[0]
            .text
        )
        assert res["frontmatter_tags"] == ["docs"]


async def test_set_tags_gate_elicit_accept_applies(workspaces_dir, mcp_server):
    mcp, _ = mcp_server

    async def accept(message, response_type, params, context):
        return {"value": "potwierdzam"}

    async with Client(mcp, elicitation_handler=accept) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        note_id = json.loads(
            (
                await client.call_tool(
                    "save_note", {"title": "T3", "content": "x", "tags": ["python", "work"]}
                )
            )
            .content[0]
            .text
        )["note_id"]
        res = json.loads(
            (await client.call_tool("set_tags", {"note_id": note_id, "tags": ["docs"]}))
            .content[0]
            .text
        )
        assert res["frontmatter_tags"] == ["docs"]  # elicit accepted -> applied without confirm


async def test_set_tags_gate_elicit_decline_keeps(workspaces_dir, mcp_server):
    from fastmcp.client.elicitation import ElicitResult

    mcp, _ = mcp_server

    async def decline(message, response_type, params, context):
        return ElicitResult(action="decline")

    async with Client(mcp, elicitation_handler=decline) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        note_id = json.loads(
            (
                await client.call_tool(
                    "save_note", {"title": "T4", "content": "x", "tags": ["python", "work"]}
                )
            )
            .content[0]
            .text
        )["note_id"]
        res = json.loads(
            (await client.call_tool("set_tags", {"note_id": note_id, "tags": ["docs"]}))
            .content[0]
            .text
        )
        assert res.get("cancelled") is True
        # unchanged
        note = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )
        assert set(note["tags"]) == {"python", "work"}


async def test_edit_note_overwrite_gate_fallback(workspaces_dir, mcp_server):
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
        assert res["requires_confirmation"] is True
        assert res["overwrites_content"] is True


async def test_edit_note_overwrite_confirm_applies(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        note_id = json.loads(
            (await client.call_tool("save_note", {"title": "E2", "content": "stara"}))
            .content[0]
            .text
        )["note_id"]
        sha = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )["sha"]
        res = json.loads(
            (
                await client.call_tool(
                    "edit_note",
                    {
                        "note_id": note_id,
                        "expected_sha": sha,
                        "content": "nowa",
                        "confirm": True,
                    },
                )
            )
            .content[0]
            .text
        )
        assert res.get("requires_confirmation") is None
        assert res["note_id"] == note_id


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


async def test_edit_note_stale_sha_rejected_before_confirm_gate(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        saved = await client.call_tool("save_note", {"title": "Stale2", "content": "stara"})
        note_id = json.loads(saved.content[0].text)["note_id"]
        stale_sha = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )["sha"]
        # Bump the sha with an unrelated edit so stale_sha is now out of date.
        await client.call_tool(
            "edit_note",
            {
                "note_id": note_id,
                "expected_sha": stale_sha,
                "mode": "append",
                "content": " bump",
            },
        )

        result = await client.call_tool(
            "edit_note",
            {"note_id": note_id, "expected_sha": stale_sha, "content": "nowa"},
        )

        data = json.loads(result.content[0].text)
        assert "current_sha" not in data
        assert "requires_confirmation" not in data


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
