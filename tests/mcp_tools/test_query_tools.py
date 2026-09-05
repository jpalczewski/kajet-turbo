"""list_notes/search/grep/export/reindex tool coverage."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from kajet_turbo.repositories.git import GitRepository
from tests.mcp_tools.helpers import call_json


async def test_list_notes(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool(
            "save_note",
            {
                "title": "Notatka 1",
                "content": "treść 1",
                "tags": ["python"],
                "workspace": "test-ws",
            },
        )
        await client.call_tool(
            "save_note",
            {"title": "Notatka 2", "content": "treść 2", "tags": ["js"], "workspace": "test-ws"},
        )
        result = await client.call_tool("list_notes", {"workspace": "test-ws"})
        assert "Notatka 1" in result.content[0].text
        assert "Notatka 2" in result.content[0].text


async def test_list_notes_sort_title(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool(
            "save_note", {"title": "Zebra", "content": "z", "workspace": "test-ws"}
        )
        await client.call_tool(
            "save_note", {"title": "Apple", "content": "a", "workspace": "test-ws"}
        )
        result = await client.call_tool("list_notes", {"workspace": "test-ws", "sort": "title"})
        text = result.content[0].text
        assert text.index("Apple") < text.index("Zebra")


async def test_search_notes_fts_fallback(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool(
            "save_note",
            {
                "title": "Python asyncio guide",
                "content": "Tutorial o coroutines.",
                "tags": [],
                "workspace": "test-ws",
            },
        )
        await client.call_tool(
            "save_note",
            {
                "title": "JavaScript intro",
                "content": "Podstawy JS.",
                "tags": [],
                "workspace": "test-ws",
            },
        )
        result = await client.call_tool(
            "search_notes", {"query": "asyncio", "workspace": "test-ws"}
        )
        assert "Python asyncio guide" in result.content[0].text
        assert "JavaScript intro" not in result.content[0].text


async def test_search_notes_finds_note_by_tag_only(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool(
            "save_note",
            {
                "title": "Rozmowa",
                "content": "",
                "tags": ["alice"],
                "folder": "książki/Alice",
                "workspace": "test-ws",
            },
        )
        result = await client.call_tool("search_notes", {"query": "alice", "workspace": "test-ws"})
        assert "Rozmowa" in result.content[0].text


async def test_search_notes_folder_narrowing(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool(
            "save_note",
            {"title": "In scope", "content": "keyword here", "folder": "a", "workspace": "test-ws"},
        )
        await client.call_tool(
            "save_note",
            {
                "title": "Out of scope",
                "content": "keyword here",
                "folder": "b",
                "workspace": "test-ws",
            },
        )
        result = await client.call_tool(
            "search_notes", {"query": "keyword", "folder": "a", "workspace": "test-ws"}
        )
        text = result.content[0].text
        assert "In scope" in text
        assert "Out of scope" not in text


@pytest.mark.parametrize(
    "workspace_arg",
    [{"workspace": "all"}, {}],
    ids=["workspace=all", "workspace omitted"],
)
async def test_search_notes_all_workspaces(workspaces_dir, mcp_server, workspace_arg):
    """workspace="all" and omitting `workspace` entirely must behave identically (#248:
    there is no session-active workspace to fall back to, so the omitted case defaults
    to searching everything rather than raising)."""
    ws2 = workspaces_dir / "drugi-ws"
    ws2.mkdir()
    GitRepository.init(str(ws2))
    mcp_server.workspace_repo.grant_access("u1", "drugi-ws")

    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool(
            "save_note",
            {
                "title": "Notatka w ws1",
                "content": "Python content.",
                "tags": [],
                "workspace": "test-ws",
            },
        )
        await client.call_tool(
            "save_note",
            {
                "title": "Notatka w ws2",
                "content": "Python content.",
                "tags": [],
                "workspace": "drugi-ws",
            },
        )
        result = await client.call_tool("search_notes", {"query": "Python", **workspace_arg})
        text = result.content[0].text
        assert "ws1" in text or "Notatka w ws1" in text
        assert "ws2" in text or "Notatka w ws2" in text


async def test_search_all_excludes_opted_out_workspace_but_named_search_finds_it(
    workspaces_dir, mcp_server
):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool(
            "save_note",
            {
                "title": "Private search note",
                "content": "selective-keyword",
                "tags": [],
                "workspace": "test-ws",
            },
        )
        await client.call_tool(
            "set_workspace_setting",
            {"name": "test-ws", "setting": "include_in_search_all", "value": False},
        )

    async with Client(mcp) as client:
        all_result = await client.call_tool(
            "search_notes", {"query": "selective-keyword", "workspace": "all"}
        )
        named_result = await client.call_tool(
            "search_notes", {"query": "selective-keyword", "workspace": "test-ws"}
        )

    assert all_result.content == []
    assert "Private search note" in named_result.content[0].text


async def test_search_notes_unknown_workspace_lists_available(workspaces_dir, mcp_server):
    """Folded from the deleted test_workspace_session_tools.py: an explicit unknown
    workspace name is still rejected with a ToolError whose JSON body names every
    workspace the caller can actually reach."""
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("search_notes", {"query": "anything", "workspace": "no-such-ws"})
    data = json.loads(str(exc_info.value))
    assert data["available"] == ["test-ws"]


async def test_search_notes_rejects_active_literal(workspaces_dir, mcp_server):
    """#248 removed the session-active workspace concept entirely; workspace="active"
    must be rejected with an error naming the change, never silently reinterpreted as
    "all" or as a literal workspace named "active"."""
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="workspace='active' was removed"):
            await client.call_tool("search_notes", {"query": "anything", "workspace": "active"})


async def test_grep_notes_finds_literal_line(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool(
            "save_note",
            {
                "title": "Notes",
                "content": "line one\nmafioso appears\nline three\n",
                "workspace": "test-ws",
            },
        )
        result = await client.call_tool(
            "grep_notes", {"pattern": "mafioso", "workspace": "test-ws"}
        )
        assert "mafioso appears" in result.content[0].text


async def test_export_folder(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool(
            "save_note",
            {"title": "One", "content": "body one", "folder": "docs", "workspace": "test-ws"},
        )
        await client.call_tool(
            "save_note",
            {"title": "Two", "content": "body two", "folder": "docs", "workspace": "test-ws"},
        )
        result = await client.call_tool("export_folder", {"folder": "docs", "workspace": "test-ws"})
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
        reindex_result = await call_json(client, "reindex_workspace", {"workspace": "test-ws"})
        assert reindex_result["count"] == 1
        search_result = await client.call_tool(
            "search_notes", {"query": "Reindexed", "workspace": "test-ws"}
        )
        assert "Reindexed note" in search_result.content[0].text


async def test_entries_in_filters_by_period_and_folder(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        wanted = await call_json(
            client,
            "save_note",
            {
                "title": "Wanted",
                "content": "",
                "folder": "journal/2026",
                "occurred_at": "2026-03-22",
                "workspace": "test-ws",
            },
        )
        await call_json(
            client,
            "save_note",
            {
                "title": "Sibling",
                "content": "",
                "folder": "journals-old",
                "occurred_at": "2026-03-22",
                "workspace": "test-ws",
            },
        )
        await call_json(
            client,
            "save_note",
            {"title": "Summary", "content": "", "period": "2026-W12", "workspace": "test-ws"},
        )

        result = await call_json(
            client,
            "entries_in",
            {"period": "2026-W12", "folder": "journal", "workspace": "test-ws"},
        )

        assert [note["note_id"] for note in result] == [wanted["note_id"]]


async def test_entries_in_rejects_invalid_period(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="nope"):
            await client.call_tool("entries_in", {"period": "nope", "workspace": "test-ws"})


async def test_entries_in_collection_resolves_to_its_folder(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await call_json(
            client,
            "define_collection",
            {
                "name": "journal",
                "grain": "day",
                "cardinality": "one",
                "folder": "journal/{year}/{month}",
                "title": "{date}",
                "workspace": "test-ws",
            },
        )
        wanted = await call_json(
            client,
            "save_note",
            {
                "title": "Wanted",
                "content": "",
                "folder": "journal/2026",
                "occurred_at": "2026-03-22",
                "workspace": "test-ws",
            },
        )
        await call_json(
            client,
            "save_note",
            {
                "title": "Sibling",
                "content": "",
                "folder": "journals-old",
                "occurred_at": "2026-03-22",
                "workspace": "test-ws",
            },
        )

        result = await call_json(
            client,
            "entries_in",
            {"period": "2026-W12", "collection": "journal", "workspace": "test-ws"},
        )

        assert [note["note_id"] for note in result] == [wanted["note_id"]]


async def test_entries_in_rejects_folder_and_collection_together(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await call_json(
            client,
            "define_collection",
            {
                "name": "journal",
                "grain": "day",
                "cardinality": "one",
                "folder": "journal/{year}/{month}",
                "title": "{date}",
                "workspace": "test-ws",
            },
        )

        with pytest.raises(ToolError, match=r"folder.*collection|collection.*folder"):
            await client.call_tool(
                "entries_in",
                {
                    "period": "2026-W12",
                    "folder": "journal",
                    "collection": "journal",
                    "workspace": "test-ws",
                },
            )
