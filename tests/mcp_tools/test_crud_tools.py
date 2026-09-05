"""save/get/get_notes/outline/file/delete/edit modes/move tool coverage."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.mcp_tools.helpers import call_json, save_and_get_sha
from tests.services.conftest import workspace_target


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


async def test_save_and_get_note_with_temporal_metadata(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        saved = await call_json(
            client,
            "save_note",
            {"title": "Weekly summary", "content": "Body", "period": "2026-W12"},
        )
        note = await call_json(client, "get_note", {"note_id": saved["note_id"]})
        assert note["occurred_at"] is None
        assert note["period"] == "2026-W12"


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
        assert "Note not found: note_id=bad-id" in text


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


async def test_save_note_reports_ambiguous_wikilink_warning(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    service = mcp_server.note_service
    assert service is not None
    ws_path = str(workspaces_dir / "test-ws")
    service.save(workspace_target("u1", "test-ws", ws_path), "README", "near", [], folder="Project")
    service.save(workspace_target("u1", "test-ws", ws_path), "README", "far", [], folder="Archive")

    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        result = await client.call_tool(
            "save_note",
            {"title": "Source", "folder": "Project", "content": "[[README]]"},
        )

    assert json.loads(result.content[0].text)["warnings"] == [
        {
            "kind": "ambiguous_wikilink",
            "target": "README",
            "resolved_to": "Project/README",
            "alternatives": ["Archive/README"],
        }
    ]


async def test_save_note_reports_case_corrected_wikilink_warning(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool("save_note", {"title": "Plan projektu", "content": "cel"})
        result = await call_json(
            client, "save_note", {"title": "Source", "content": "[[plan projektu]]"}
        )

    assert result["warnings"] == [
        {
            "kind": "case_corrected_wikilink",
            "target": "plan projektu",
            "resolved_to": "Plan projektu",
            "alternatives": [],
        }
    ]


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
        assert json.loads(edit_result.content[0].text) == {
            "note_id": note_id,
            "replaced": None,
            "warnings": [],
            "temporal_warnings": [],
            "occurred_at": None,
            "period": None,
        }
        get_result = await client.call_tool("get_note", {"note_id": note_id})
        content = json.loads(get_result.content[0].text)["content"]
        assert "- Pierwsze\n- Drugie" in content


async def test_edit_note_echoes_temporal_fields_without_leaking_content_in_slow_sync_log(
    workspaces_dir, mcp_server, monkeypatch, capsys
):
    from kajet_turbo import concurrency
    from kajet_turbo.log import setup_logging
    from tests.helpers import entries_named, read_log_entries

    setup_logging()
    monkeypatch.setattr(concurrency, "_SLOW_SYNC_MS", 1.0)  # log every dispatch

    mcp, _ = mcp_server
    secret_content = "Body with a private detail nobody else should read"
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        saved = await call_json(
            client,
            "save_note",
            {"title": "Weekly summary", "content": secret_content, "period": "2026-W12"},
        )
        note = await call_json(client, "get_note", {"note_id": saved["note_id"]})

        result = await call_json(
            client,
            "edit_note",
            {
                "note_id": saved["note_id"],
                "expected_sha": note["sha"],
                "occurred_at": "2026-03-22",
            },
        )

        # Setting occurred_at clears the mutually-exclusive period — echoed back so the
        # caller sees the side effect instead of discovering it on a later get_note.
        assert result["occurred_at"] == "2026-03-22"
        assert result["period"] is None

        slow = entries_named(read_log_entries(capsys), "slow_sync")
        update_entries = [e for e in slow if e["op"] == "NoteService.update"]
        assert update_entries  # partial(...) previously had no __qualname__, falling to repr(fn)
        for entry in slow:
            dumped = json.dumps(entry)
            assert secret_content not in dumped
            assert "Weekly summary" not in dumped


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
        with pytest.raises(ToolError, match="old_str is ambiguous"):
            await client.call_tool(
                "edit_note",
                {
                    "note_id": note_id,
                    "expected_sha": sha,
                    "mode": "replace_text",
                    "old_str": "foo",
                    "new_str": "qux",
                },
            )


async def test_edit_note_replace_all_reports_count(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        saved = await client.call_tool(
            "save_note", {"title": "Doc", "content": "foo bar foo baz foo"}
        )
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
                "old_str": "foo",
                "new_str": "qux",
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


async def test_get_note_by_title(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool(
            "save_note", {"title": "2026-08-22", "content": "wpis dzienny", "folder": "Dziennik"}
        )
        note = await call_json(client, "get_note", {"title": "2026-08-22"})

    assert (note["title"], note["folder"]) == ("2026-08-22", "Dziennik")
    assert note["content"] == "wpis dzienny"


async def test_get_note_by_title_takes_folder_as_a_path_suffix(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    service = mcp_server.note_service
    assert service is not None
    ws_path = str(workspaces_dir / "test-ws")
    service.save(
        workspace_target("u1", "test-ws", ws_path),
        "README",
        "backlog",
        [],
        folder="Project/backlog",
    )
    service.save(
        workspace_target("u1", "test-ws", ws_path), "README", "archive", [], folder="Archive"
    )

    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        note = await call_json(client, "get_note", {"title": "README", "folder": "backlog"})

    assert note["content"] == "backlog"


async def test_get_note_by_ambiguous_title_lists_the_candidates(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    service = mcp_server.note_service
    assert service is not None
    ws_path = str(workspaces_dir / "test-ws")
    service.save(workspace_target("u1", "test-ws", ws_path), "README", "near", [], folder="Project")
    service.save(workspace_target("u1", "test-ws", ws_path), "README", "far", [], folder="Archive")

    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        with pytest.raises(ToolError, match="Niejednoznaczne") as excinfo:
            await client.call_tool("get_note", {"title": "README"})

    assert "Project" in str(excinfo.value) and "Archive" in str(excinfo.value)


async def test_get_note_by_ambiguous_title_still_errors_when_one_candidate_is_at_the_root(
    workspaces_dir, mcp_server
):
    """A root note ranks first for a bare title, which must not pass for an exact match."""
    mcp, _ = mcp_server
    service = mcp_server.note_service
    assert service is not None
    ws_path = str(workspaces_dir / "test-ws")
    service.save(workspace_target("u1", "test-ws", ws_path), "README", "root", [])
    service.save(
        workspace_target("u1", "test-ws", ws_path), "README", "nested", [], folder="Project"
    )

    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        with pytest.raises(ToolError, match="Niejednoznaczne"):
            await client.call_tool("get_note", {"title": "README"})


async def test_get_note_by_title_case_mismatch_returns_not_found(workspaces_dir, mcp_server):
    """Unlike wikilinks, get_note(title=...) stays exact — no casefold fallback."""
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool("save_note", {"title": "Plan projektu", "content": "cel"})
        with pytest.raises(ToolError, match="Note not found"):
            await client.call_tool("get_note", {"title": "plan projektu"})


async def test_get_note_rejects_an_ambiguous_call_shape(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        saved = await call_json(client, "save_note", {"title": "A", "content": "x"})
        with pytest.raises(ToolError, match="exactly one of note_id or title"):
            await client.call_tool("get_note", {"note_id": saved["note_id"], "title": "A"})
        with pytest.raises(ToolError, match="Provide note_id or title"):
            await client.call_tool("get_note", {})
        with pytest.raises(ToolError, match="folder only works with title"):
            await client.call_tool("get_note", {"note_id": saved["note_id"], "folder": "x"})
        with pytest.raises(ToolError, match="Note not found"):
            await client.call_tool("get_note", {"title": "Nie ma takiej"})


async def test_edit_note_text_modes_take_old_str_and_new_str(workspaces_dir, mcp_server):
    """The wire contract from issue #38: the text modes are an old_str/new_str pair."""
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        note_id, sha = await save_and_get_sha(client, "Pair", "Hello world.")

        await call_json(
            client,
            "edit_note",
            {
                "note_id": note_id,
                "expected_sha": sha,
                "mode": "replace_text",
                "old_str": "world",
                "new_str": "earth",
            },
        )
        assert (await call_json(client, "get_note", {"note_id": note_id}))[
            "content"
        ] == "Hello earth."


async def test_edit_note_insert_after_inserts_new_str_at_the_anchor(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        note_id, sha = await save_and_get_sha(client, "List", "- A\n- B\n")

        await call_json(
            client,
            "edit_note",
            {
                "note_id": note_id,
                "expected_sha": sha,
                "mode": "insert_after",
                "old_str": "- A",
                "new_str": "- A.5",
            },
        )
        content = (await call_json(client, "get_note", {"note_id": note_id}))["content"]
        assert "- A\n- A.5\n- B" in content


async def test_edit_note_rejects_a_parameter_from_another_mode(workspaces_dir, mcp_server):
    """The apply_edit rejection surfaces as a ToolError, with the note left untouched.

    The full mode/parameter matrix is covered in tests/markdown/test_note_edit.py — this
    only proves the wiring, so one case is enough for a fixture this expensive. Asserting
    the exact message (not a substring) also proves this ValueError reaches the client
    clean via logged_tool's SERVICE_ERRORS mapping, not wrapped in fastmcp's own generic
    "Error calling tool 'edit_note': ..." text — through the real mounted build_mcp()
    stack, not just a unit test of the wrapper in isolation.
    """
    args = {"mode": "replace_text", "old_str": "world", "content": "earth"}
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        note_id, sha = await save_and_get_sha(client, "Strict", "Hello world.")

        with pytest.raises(ToolError) as exc_info:
            await client.call_tool("edit_note", {"note_id": note_id, "expected_sha": sha, **args})
        assert str(exc_info.value) == (
            "Mode 'replace_text' does not take content; it takes old_str and new_str."
        )

        assert (await call_json(client, "get_note", {"note_id": note_id}))[
            "content"
        ] == "Hello world."


async def test_edit_note_without_content_edits_metadata_only(workspaces_dir, mcp_server):
    """Renaming a note must not go through as an overwrite with an empty body."""
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        note_id, sha = await save_and_get_sha(client, "Before", "Body stays.")

        await call_json(
            client,
            "edit_note",
            {"note_id": note_id, "expected_sha": sha, "title": "After", "tags": ["x"]},
        )
        note = await call_json(client, "get_note", {"note_id": note_id})
        assert note["title"] == "After"
        assert note["tags"] == ["x"]
        assert note["content"] == "Body stays."


async def test_edit_note_rejects_an_unknown_parameter(workspaces_dir, mcp_server):
    """fastmcp rejects extras on a tool signature — asserted, not assumed.

    The batch path gets this from ToolInput(extra="forbid"); here it is an inherited
    framework default, and a relaxation of it would silently resurrect issue #38.
    """
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        note_id, sha = await save_and_get_sha(client, "Typo", "Hello world.")

        with pytest.raises(ToolError, match="old_text"):
            await client.call_tool(
                "edit_note",
                {
                    "note_id": note_id,
                    "expected_sha": sha,
                    "mode": "replace_text",
                    "old_text": "world",
                    "new_str": "earth",
                },
            )
        assert (await call_json(client, "get_note", {"note_id": note_id}))[
            "content"
        ] == "Hello world."
