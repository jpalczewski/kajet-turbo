import json

from fastmcp import Client

from kajet_turbo.repositories.git import GitRepository


async def test_list_workspaces(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        result = await client.call_tool("list_workspaces")
    assert "test-ws" in result.content[0].text


async def test_activate_workspace(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        result = await client.call_tool("activate_workspace", {"name": "test-ws"})
    assert "test-ws" in result.content[0].text


async def test_activate_nonexistent_workspace(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        result = await client.call_tool("activate_workspace", {"name": "nie-istnieje"})
    assert "aktywny" not in result.content[0].text.lower()


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
        await client.call_tool("delete_note", {"note_id": note_id})
        get_result = await client.call_tool("get_note", {"note_id": note_id})
        assert "error" in json.loads(get_result.content[0].text)


async def test_edit_note_overwrite(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool(
            "save_note", {"title": "Stary tytuł", "content": "stara treść"}
        )
        note_id = json.loads(save_result.content[0].text)["note_id"]
        await client.call_tool(
            "edit_note", {"note_id": note_id, "title": "Nowy tytuł", "content": "nowa treść"}
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
        edit_result = await client.call_tool(
            "edit_note",
            {
                "note_id": note_id,
                "mode": "append",
                "target_heading": "## Zadania",
                "content": "- Drugie",
            },
        )
        assert json.loads(edit_result.content[0].text) == {"note_id": note_id}
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
        edit_result = await client.call_tool(
            "edit_note",
            {"note_id": note_id, "mode": "replace_text", "old_text": "foo", "content": "qux"},
        )
        assert "error" in json.loads(edit_result.content[0].text)


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

        assert json.loads(folders.content[0].text) == ["", "archive"]
        assert json.loads(move_result.content[0].text) == {
            "note_id": note_id,
            "folder": "archive",
        }
        assert (ws_path / "archive" / "Move me.md").exists()


async def test_list_notes(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool(
            "save_note", {"title": "Notatka 1", "content": "treść 1", "tags": ["python"]}
        )
        await client.call_tool(
            "save_note", {"title": "Notatka 2", "content": "treść 2", "tags": ["js"]}
        )
        result = await client.call_tool("list_notes", {})
        assert "Notatka 1" in result.content[0].text
        assert "Notatka 2" in result.content[0].text


async def test_search_notes_fts_fallback(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool(
            "save_note",
            {"title": "Python asyncio guide", "content": "Tutorial o coroutines.", "tags": []},
        )
        await client.call_tool(
            "save_note", {"title": "JavaScript intro", "content": "Podstawy JS.", "tags": []}
        )
        result = await client.call_tool("search_notes", {"query": "asyncio"})
        assert "Python asyncio guide" in result.content[0].text
        assert "JavaScript intro" not in result.content[0].text


async def test_search_notes_all_workspaces(workspaces_dir, mcp_server):
    ws2 = workspaces_dir / "drugi-ws"
    ws2.mkdir()
    GitRepository.init(str(ws2))

    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool(
            "save_note", {"title": "Notatka w ws1", "content": "Python content.", "tags": []}
        )
        await client.call_tool("activate_workspace", {"name": "drugi-ws"})
        await client.call_tool(
            "save_note", {"title": "Notatka w ws2", "content": "Python content.", "tags": []}
        )
        result = await client.call_tool("search_notes", {"query": "Python", "workspace": "all"})
        text = result.content[0].text
        assert "ws1" in text or "Notatka w ws1" in text
        assert "ws2" in text or "Notatka w ws2" in text


async def test_reindex_workspace(workspaces_dir, mcp_server):
    from kajet_turbo.workspace import note_filepath, write_note_file

    ws_path = workspaces_dir / "test-ws"
    path = note_filepath(str(ws_path), "", "Reindexed note")
    write_note_file(
        path,
        "zzz1111",
        "Reindexed note",
        ["test"],
        "2026-06-08T12:00:00+00:00",
        "2026-06-08T12:00:00+00:00",
        "treść",
    )

    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        reindex_result = await client.call_tool("reindex_workspace")
        assert (
            "ok" in reindex_result.content[0].text.lower()
            or "reindeks" in reindex_result.content[0].text.lower()
        )
        search_result = await client.call_tool("search_notes", {"query": "Reindexed"})
        assert "Reindexed note" in search_result.content[0].text
