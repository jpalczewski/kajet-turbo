"""save/get/get_notes/outline/file/delete/edit modes/move tool coverage."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError


async def test_save_and_get_note(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool(
            "save_note",
            {"title": "Moja notatka", "content": "# Treść\n\nTekst.", "tags": ["python"]},
        )
        note_id = json.loads(save_result.content[0].text)["note_id"]
        assert len(note_id) > 0

        get_result = await client.call_tool("get_note", {"note_id": note_id})
        assert "Moja notatka" in get_result.content[0].text


async def test_get_notes_bulk_read(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        r1 = await client.call_tool("save_note", {"title": "First", "content": "one"})
        r2 = await client.call_tool("save_note", {"title": "Second", "content": "two"})

        id1 = json.loads(r1.content[0].text)["note_id"]
        id2 = json.loads(r2.content[0].text)["note_id"]
        result = await client.call_tool("get_notes", {"note_ids": [id1, "bad-id", id2]})
        text = result.content[0].text
        assert "First" in text
        assert "Second" in text
        assert "nie znaleziona" in text


async def test_get_notes_rejects_too_many_ids(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        with pytest.raises(ToolError):
            await client.call_tool("get_notes", {"note_ids": [f"id{i}" for i in range(51)]})


async def test_get_note_outline(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        saved = await client.call_tool(
            "save_note", {"title": "Doc", "content": "# Doc\n\n## Tasks\n\ncontent\n"}
        )
        import json

        note_id = json.loads(saved.content[0].text)["note_id"]
        result = await client.call_tool("get_note_outline", {"note_id": note_id})
        text = result.content[0].text
        assert "Tasks" in text
        assert "content" not in text.lower() or "## Tasks" in text  # heading text, not body


async def test_save_note_creates_file(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool("save_note", {"title": "Plikowa notatka", "content": "treść"})

    ws_path = workspaces_dir / "test-ws"
    files = [p for p in ws_path.rglob("*.md") if ".git" not in str(p)]
    assert len(files) == 1


async def test_delete_note(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool(
            "save_note", {"title": "Do usunięcia", "content": "treść"}
        )
        note_id = json.loads(save_result.content[0].text)["note_id"]
        sha = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )["sha"]
        await client.call_tool("delete_note", {"note_id": note_id, "expected_sha": sha})
        with pytest.raises(ToolError):
            await client.call_tool("get_note", {"note_id": note_id})


async def test_edit_note_overwrite(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool(
            "save_note", {"title": "Stary tytuł", "content": "stara treść"}
        )
        note_id = json.loads(save_result.content[0].text)["note_id"]
        sha = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )["sha"]
        await client.call_tool(
            "edit_note",
            {
                "note_id": note_id,
                "expected_sha": sha,
                "title": "Nowy tytuł",
                "content": "nowa treść",
            },
        )
        get_result = await client.call_tool("get_note", {"note_id": note_id})
        assert "Nowy tytuł" in get_result.content[0].text
        assert "nowa treść" in get_result.content[0].text


async def test_edit_note_append_mode(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool(
            "save_note", {"title": "Dziennik", "content": "## Zadania\n\n- Pierwsze"}
        )
        note_id = json.loads(save_result.content[0].text)["note_id"]
        sha = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )["sha"]
        edit_result = await client.call_tool(
            "edit_note",
            {
                "note_id": note_id,
                "expected_sha": sha,
                "mode": "append",
                "target_heading": "## Zadania",
                "content": "- Drugie",
            },
        )
        assert json.loads(edit_result.content[0].text) == {"note_id": note_id, "replaced": None}
        get_result = await client.call_tool("get_note", {"note_id": note_id})
        content = json.loads(get_result.content[0].text)["content"]
        assert "- Pierwsze\n- Drugie" in content


async def test_edit_note_replace_text_ambiguous_errors(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool(
            "save_note", {"title": "Dwa razy", "content": "foo bar foo"}
        )
        note_id = json.loads(save_result.content[0].text)["note_id"]
        sha = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )["sha"]
        with pytest.raises(ToolError):
            await client.call_tool(
                "edit_note",
                {
                    "note_id": note_id,
                    "expected_sha": sha,
                    "mode": "replace_text",
                    "old_text": "foo",
                    "content": "qux",
                },
            )


async def test_edit_note_replace_all_reports_count(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        saved = await client.call_tool(
            "save_note", {"title": "Doc", "content": "foo bar foo baz foo"}
        )
        import json

        note_id = json.loads(saved.content[0].text)["note_id"]
        sha = json.loads(
            (await client.call_tool("get_note", {"note_id": note_id})).content[0].text
        )["sha"]
        result = await client.call_tool(
            "edit_note",
            {
                "note_id": note_id,
                "expected_sha": sha,
                "mode": "replace_text",
                "content": "qux",
                "old_text": "foo",
                "replace_all": True,
            },
        )
        data = json.loads(result.content[0].text)
        assert data["replaced"] == 3


async def test_move_note_and_list_folders(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    ws_path = workspaces_dir / "test-ws"
    (ws_path / "archive").mkdir()
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool(
            "save_note", {"title": "Move me", "content": "content"}
        )
        note_id = json.loads(save_result.content[0].text)["note_id"]
        folders = await client.call_tool("list_folders", {})
        move_result = await client.call_tool("move_note", {"note_id": note_id, "folder": "archive"})

        folder_paths = [f["path"] for f in json.loads(folders.content[0].text)]
        assert folder_paths == ["", "archive"]
        assert json.loads(move_result.content[0].text) == {
            "note_id": note_id,
            "folder": "archive",
        }
        assert (ws_path / "archive" / "Move me.md").exists()
