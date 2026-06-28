import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from kajet_turbo.repositories.git import GitRepository


async def test_list_workspaces(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        result = await client.call_tool("list_workspaces")
    names = [w["name"] for w in json.loads(result.content[0].text)["workspaces"]]
    assert "test-ws" in names


async def test_activate_workspace(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        result = await client.call_tool("activate_workspace", {"name": "test-ws"})
    assert "test-ws" in result.content[0].text


async def test_activate_nonexistent_workspace(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("activate_workspace", {"name": "nie-istnieje"})


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
        await client.call_tool(
            "edit_note",
            {"note_id": note_id, "title": "Nowy tytuł", "content": "nowa treść", "confirm": True},
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
        with pytest.raises(ToolError):
            await client.call_tool(
                "edit_note",
                {"note_id": note_id, "mode": "replace_text", "old_text": "foo", "content": "qux"},
            )


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

        st = json.loads(
            (
                await client.call_tool(
                    "set_tags", {"note_id": note_id, "tags": ["docs"], "confirm": True}
                )
            )
            .content[0]
            .text
        )
        assert st["frontmatter_tags"] == ["docs"]


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
        res = json.loads(
            (await client.call_tool("edit_note", {"note_id": note_id, "content": "nowa"}))
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
        res = json.loads(
            (
                await client.call_tool(
                    "edit_note", {"note_id": note_id, "content": "nowa", "confirm": True}
                )
            )
            .content[0]
            .text
        )
        assert res.get("requires_confirmation") is None
        assert res["note_id"] == note_id


# --- active workspace scope: claude.ai conversations share user/client, not MCP session ---


async def test_fresh_authenticated_session_does_not_inherit_workspace(
    authed_workspaces_dir, authed_mcp_server
):
    """A second Claude conversation must not inherit the first one's active workspace."""
    mcp, _ = authed_mcp_server
    async with Client(mcp) as client_a:
        await client_a.call_tool("activate_workspace", {"name": "test-ws"})

    async with Client(mcp) as client_b:
        with pytest.raises(ToolError, match="activate_workspace"):
            await client_b.call_tool("save_note", {"title": "Should fail", "content": "body"})


async def test_two_authenticated_sessions_keep_separate_workspaces(
    authed_workspaces_dir, authed_mcp_server, git_workspace_factory
):
    mcp, _ = authed_mcp_server
    git_workspace_factory("workspaces/u1/drugi-ws")
    authed_mcp_server.workspace_repo.grant_access("u1", "drugi-ws")

    async with Client(mcp) as client_a:
        await client_a.call_tool("activate_workspace", {"name": "test-ws"})
        first = json.loads(
            (await client_a.call_tool("save_note", {"title": "First", "content": "body"}))
            .content[0]
            .text
        )["note_id"]

    async with Client(mcp) as client_b:
        await client_b.call_tool("activate_workspace", {"name": "drugi-ws"})
        second = json.loads(
            (await client_b.call_tool("save_note", {"title": "Second", "content": "body"}))
            .content[0]
            .text
        )["note_id"]

    async with Client(mcp) as client_a_again:
        await client_a_again.call_tool("activate_workspace", {"name": "test-ws"})
        first_note = await client_a_again.call_tool("get_note", {"note_id": first})
        with pytest.raises(ToolError):
            await client_a_again.call_tool("get_note", {"note_id": second})

    assert "First" in first_note.content[0].text


async def test_anon_no_cross_session_persistence(workspaces_dir, mcp_server):
    """Unauthenticated sessions get no cross-session persistence (IDOR-safe)."""
    mcp, _ = mcp_server
    async with Client(mcp) as client_a:
        await client_a.call_tool("activate_workspace", {"name": "test-ws"})

    async with Client(mcp) as client_b:
        with pytest.raises(ToolError, match="activate_workspace"):
            await client_b.call_tool("save_note", {"title": "Should fail", "content": "body"})


async def test_authenticated_session_writes_to_user_scoped_path(
    authed_workspaces_dir, authed_mcp_server
):
    mcp, _ = authed_mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool("save_note", {"title": "Scoped note", "content": "body"})

    ws_path = authed_workspaces_dir / "u1" / "test-ws"
    files = [p for p in ws_path.rglob("*.md") if ".git" not in str(p)]
    assert len(files) == 1


async def test_search_all_scope_after_session_activation(authed_workspaces_dir, authed_mcp_server):
    mcp, _ = authed_mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        search_result = await client.call_tool(
            "search_notes", {"query": "anything", "workspace": "all"}
        )
    assert not search_result.is_error


async def test_same_session_fast_path_unchanged(authed_workspaces_dir, authed_mcp_server):
    """Single-session activate+save still works (Claude Code path, no DB needed on read)."""
    mcp, _ = authed_mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool(
            "save_note", {"title": "Same session", "content": "body"}
        )
    payload = json.loads(save_result.content[0].text)
    assert "error" not in payload
    assert len(payload["note_id"]) > 0


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


async def test_get_note_links(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        target_id = json.loads(
            (await client.call_tool("save_note", {"title": "Target", "content": "content"}))
            .content[0]
            .text
        )["note_id"]
        source_id = json.loads(
            (await client.call_tool("save_note", {"title": "Source", "content": "see [[Target]]"}))
            .content[0]
            .text
        )["note_id"]

        # outlinks of Source → Target
        result = json.loads(
            (await client.call_tool("get_note_links", {"note_id": source_id})).content[0].text
        )
        assert result["outlinks"] == [
            {
                "note_id": target_id,
                "title": "Target",
                "folder": "",
                "workspace": "test-ws",
                "tags": None,
                "updated_at": None,
            }
        ]
        assert result["backlinks"] == []

        # backlinks of Target → Source
        result = json.loads(
            (await client.call_tool("get_note_links", {"note_id": target_id})).content[0].text
        )
        assert result["backlinks"] == [
            {
                "note_id": source_id,
                "title": "Source",
                "folder": "",
                "workspace": "test-ws",
                "tags": None,
                "updated_at": None,
            }
        ]
        assert result["outlinks"] == []


async def test_get_note_links_not_found(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        with pytest.raises(ToolError):
            await client.call_tool("get_note_links", {"note_id": "nonexistent"})


async def test_get_note_links_include_meta(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        target_id = json.loads(
            (
                await client.call_tool(
                    "save_note", {"title": "Tagged", "content": "content", "tags": ["work"]}
                )
            )
            .content[0]
            .text
        )["note_id"]
        source_id = json.loads(
            (await client.call_tool("save_note", {"title": "Linker", "content": "[[Tagged]]"}))
            .content[0]
            .text
        )["note_id"]

        result = json.loads(
            (await client.call_tool("get_note_links", {"note_id": source_id, "include_meta": True}))
            .content[0]
            .text
        )
        entry = result["outlinks"][0]
        assert entry["note_id"] == target_id
        assert "tags" in entry
        assert entry["tags"] == ["work"]
        assert "updated_at" in entry
