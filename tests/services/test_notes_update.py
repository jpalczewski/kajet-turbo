"""update() mode/replace_all coverage for NoteService."""

from unittest.mock import patch

import pytest


def test_update_git_error_reverts_file(service, workspace):
    from kajet_turbo.repositories.git import GitError

    result = service.save("u1", "ws", str(workspace), "Oryginał", "stara treść", [])
    note_id = result["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    with (
        patch(
            "kajet_turbo.repositories.git.GitRepository.commit_file", side_effect=GitError("fail")
        ),
        pytest.raises(GitError),
    ):
        service.update(
            note_id,
            owner_id="u1",
            ws_path=str(workspace),
            expected_sha=sha,
            content="nowa treść",
        )
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.content == "stara treść"


def test_update_title_renames_file(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Old title", "content", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="New title"
    )

    assert not (workspace / "Old title.md").exists()
    assert (workspace / "New title.md").exists()


def test_update_append_mode_adds_to_section(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Dziennik", "## Zadania\n\n- Pierwsze", [])[
        "note_id"
    ]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        content="- Drugie",
        mode="append",
        target_heading="## Zadania",
    )

    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert "- Pierwsze\n- Drugie" in note.content
    # Edit produced a second commit (history grows).
    assert len(service.get_history(note_id, owner_id="u1", ws_path=str(workspace))) == 2


def test_update_replace_text_mode(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notatka", "Hello world.", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        content="earth",
        mode="replace_text",
        old_text="world",
    )

    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.content == "Hello earth."


def test_update_insert_after_mode(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Lista", "- A\n- B\n", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        content="- A.5",
        mode="insert_after",
        old_text="- A",
    )

    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert "- A\n- A.5\n- B" in note.content


def test_update_edit_mode_requires_content(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notatka", "treść", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    with pytest.raises(ValueError, match="content"):
        service.update(
            note_id,
            owner_id="u1",
            ws_path=str(workspace),
            expected_sha=sha,
            mode="append",
            target_heading=None,
        )


def test_update_replace_text_requires_content(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notatka", "Hello world.", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    with pytest.raises(ValueError, match="content jest wymagany"):
        service.update(
            note_id,
            owner_id="u1",
            ws_path=str(workspace),
            expected_sha=sha,
            content=None,
            mode="replace_text",
            old_text="world",
        )

    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.content == "Hello world."


def test_update_delete_text_mode(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Lista", "- A\n- B\n- C\n", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        mode="delete_text",
        old_text="- B\n",
    )

    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.content == "- A\n- C"


def test_update_replace_text_ambiguous_raises(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notatka", "foo bar foo", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    with pytest.raises(ValueError):
        service.update(
            note_id,
            owner_id="u1",
            ws_path=str(workspace),
            expected_sha=sha,
            content="qux",
            mode="replace_text",
            old_text="foo",
        )


def test_update_replace_text_replace_all_reports_count(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Doc", "foo bar foo baz foo", [])
    sha = service.get_history(result["note_id"], owner_id="u1", ws_path=str(workspace))[0]["sha"]
    updated = service.update(
        result["note_id"],
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        mode="replace_text",
        content="qux",
        old_text="foo",
        replace_all=True,
    )
    assert updated["replaced"] == 3
    reread = service.get_with_content(result["note_id"], "u1", str(workspace))
    assert reread.content == "qux bar qux baz qux"


def test_update_without_replace_all_replaced_is_none(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Doc", "unique text here", [])
    sha = service.get_history(result["note_id"], owner_id="u1", ws_path=str(workspace))[0]["sha"]
    updated = service.update(
        result["note_id"],
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        mode="replace_text",
        content="new",
        old_text="unique",
    )
    assert updated["replaced"] is None


def test_update_replace_all_wrong_mode_raises(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Doc", "body text", [])
    sha = service.get_history(result["note_id"], owner_id="u1", ws_path=str(workspace))[0]["sha"]
    with pytest.raises(ValueError, match="replace_all"):
        service.update(
            result["note_id"],
            owner_id="u1",
            ws_path=str(workspace),
            expected_sha=sha,
            mode="overwrite",
            content="new body",
            replace_all=True,
        )
