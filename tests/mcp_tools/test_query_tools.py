"""list_notes/search/grep/export/reindex tool coverage."""

from fastmcp import Client

from kajet_turbo.repositories.git import GitRepository


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


async def test_list_notes_sort_title(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool("save_note", {"title": "Zebra", "content": "z"})
        await client.call_tool("save_note", {"title": "Apple", "content": "a"})
        result = await client.call_tool("list_notes", {"sort": "title"})
        text = result.content[0].text
        assert text.index("Apple") < text.index("Zebra")


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


async def test_search_notes_finds_note_by_tag_only(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool(
            "save_note",
            {
                "title": "Rozmowa",
                "content": "",
                "tags": ["alice"],
                "folder": "książki/Alice",
            },
        )
        result = await client.call_tool("search_notes", {"query": "alice"})
        assert "Rozmowa" in result.content[0].text


async def test_search_notes_folder_narrowing(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool(
            "save_note", {"title": "In scope", "content": "keyword here", "folder": "a"}
        )
        await client.call_tool(
            "save_note", {"title": "Out of scope", "content": "keyword here", "folder": "b"}
        )
        result = await client.call_tool("search_notes", {"query": "keyword", "folder": "a"})
        text = result.content[0].text
        assert "In scope" in text
        assert "Out of scope" not in text


async def test_search_notes_all_workspaces(workspaces_dir, mcp_server):
    ws2 = workspaces_dir / "drugi-ws"
    ws2.mkdir()
    GitRepository.init(str(ws2))
    mcp_server.workspace_repo.grant_access("u1", "drugi-ws")

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


async def test_search_all_excludes_opted_out_workspace_but_named_search_finds_it(
    workspaces_dir, mcp_server
):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool(
            "save_note",
            {"title": "Private search note", "content": "selective-keyword", "tags": []},
        )
        await client.call_tool(
            "set_workspace_setting",
            {"name": "test-ws", "setting": "include_in_search_all", "value": False},
        )

    # Remove both persisted fallback scopes so neither assertion can accidentally use
    # activation left behind by the setup client.
    mcp_server.active_workspace_repo.delete_for_workspace("u1", "test-ws")

    async with Client(mcp) as client:
        all_result = await client.call_tool(
            "search_notes", {"query": "selective-keyword", "workspace": "all"}
        )
        named_result = await client.call_tool(
            "search_notes", {"query": "selective-keyword", "workspace": "test-ws"}
        )

    assert all_result.content == []
    assert "Private search note" in named_result.content[0].text


async def test_grep_notes_finds_literal_line(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool(
            "save_note", {"title": "Notes", "content": "line one\nmafioso appears\nline three\n"}
        )
        result = await client.call_tool("grep_notes", {"pattern": "mafioso"})
        assert "mafioso appears" in result.content[0].text


async def test_export_folder(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool(
            "save_note", {"title": "One", "content": "body one", "folder": "docs"}
        )
        await client.call_tool(
            "save_note", {"title": "Two", "content": "body two", "folder": "docs"}
        )
        result = await client.call_tool("export_folder", {"folder": "docs"})
        text = result.content[0].text
        assert "body one" in text
        assert "body two" in text


async def test_reindex_workspace(workspaces_dir, mcp_server):
    from kajet_turbo.workspace import NoteFrontmatter, note_filepath, write_note_file

    ws_path = workspaces_dir / "test-ws"
    path = note_filepath(str(ws_path), "", "Reindexed note")
    write_note_file(
        path,
        NoteFrontmatter(
            id="zzz1111",
            title="Reindexed note",
            tags=["test"],
            created_at="2026-06-08T12:00:00+00:00",
            updated_at="2026-06-08T12:00:00+00:00",
        ),
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
