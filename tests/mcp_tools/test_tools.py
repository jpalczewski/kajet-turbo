import json

from fastmcp import Client

from kajet_turbo.repositories.git import GitRepository


async def test_list_workspaces(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        result = await client.call_tool("list_workspaces")
    names = [w["name"] for w in json.loads(result.content[0].text)]
    assert "test-ws" in names


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


# --- dual-key active workspace: survives the connector's per-call session churn ---


async def test_dual_session_fallback_authenticated(authed_workspaces_dir, authed_mcp_server):
    """activate_workspace in one session, save_note in a fresh session, no re-activate.

    Mirrors the claude.ai connector opening a new MCP session per tool call: the
    authenticated user's active workspace is recovered from the DB fallback.
    """
    mcp, _ = authed_mcp_server
    async with Client(mcp) as client_a:
        await client_a.call_tool("activate_workspace", {"name": "test-ws"})

    async with Client(mcp) as client_b:
        save_result = await client_b.call_tool(
            "save_note", {"title": "Recovered note", "content": "body"}
        )
    payload = json.loads(save_result.content[0].text)
    assert "error" not in payload
    assert len(payload["note_id"]) > 0


async def test_anon_no_cross_session_persistence(workspaces_dir, mcp_server):
    """Unauthenticated sessions get no cross-session persistence (IDOR-safe)."""
    mcp, _ = mcp_server
    async with Client(mcp) as client_a:
        await client_a.call_tool("activate_workspace", {"name": "test-ws"})

    async with Client(mcp) as client_b:
        save_result = await client_b.call_tool(
            "save_note", {"title": "Should fail", "content": "body"}
        )
    payload = json.loads(save_result.content[0].text)
    assert "activate_workspace" in payload["error"]


async def test_fallback_writes_to_user_scoped_path(authed_workspaces_dir, authed_mcp_server):
    """After DB fallback, the note lands under the user-scoped path WORKSPACES_DIR/u1/test-ws."""
    mcp, _ = authed_mcp_server
    async with Client(mcp) as client_a:
        await client_a.call_tool("activate_workspace", {"name": "test-ws"})
    async with Client(mcp) as client_b:
        await client_b.call_tool("save_note", {"title": "Scoped note", "content": "body"})

    ws_path = authed_workspaces_dir / "u1" / "test-ws"
    files = [p for p in ws_path.rglob("*.md") if ".git" not in str(p)]
    assert len(files) == 1


async def test_search_all_scope_after_fallback(authed_workspaces_dir, authed_mcp_server):
    """search_notes(workspace='all') works after fallback — guards the notes.py
    active_user_id read that rehydration must satisfy."""
    mcp, _ = authed_mcp_server
    async with Client(mcp) as client_a:
        await client_a.call_tool("activate_workspace", {"name": "test-ws"})
    async with Client(mcp) as client_b:
        search_result = await client_b.call_tool(
            "search_notes", {"query": "anything", "workspace": "all"}
        )
    payload = json.loads(search_result.content[0].text)
    assert isinstance(payload, list)  # not an {"error": ...} dict


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
