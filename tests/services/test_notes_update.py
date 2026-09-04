"""update() mode/replace_all coverage for NoteService."""

import time
from unittest.mock import patch

import pytest

from kajet_turbo import perf
from tests.services.helpers import head_sha, make_flaky_db_write


def test_update_perf_span_excludes_git_commit_from_db_ms(service, workspace):
    """#155's observability decision: commit_rows_then_tree runs the git commit under
    perf.excluded_from("db_ms"), so db_ms and git_ms never double-count the same window
    and their sum never exceeds the call's actual wall time."""
    result = service.save("u1", "ws", str(workspace), "Perf update", "body", [])
    note_id = result["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    started = time.monotonic()
    with perf.perf_span() as span:
        service.update(
            note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha, content="new body"
        )
    duration_ms = (time.monotonic() - started) * 1000

    assert span is not None
    assert span.fields["git_ms"] > 0
    assert "db_ms" in span.fields
    assert span.fields["db_ms"] + span.fields["git_ms"] <= duration_ms


def test_update_git_error_reverts_file(service, workspace):
    from kajet_turbo.repositories.git import GitError

    result = service.save("u1", "ws", str(workspace), "Oryginał", "stara treść", [])
    note_id = result["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    # update()'s write leg commits through staged_workspace_change, which always calls
    # commit_changes (even for a single file) so single- and multi-file writes share one
    # rollback path.
    with (
        patch(
            "kajet_turbo.repositories.git.GitRepository.commit_changes",
            side_effect=GitError("fail"),
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


def test_update_rename_git_error_reverts_to_old_path(service, workspace):
    """#118: rename+content used to be two commits. If the second (content) commit
    failed, the file ended up at the new path with old content, but the DB row still
    pointed at the old path — the note became unreachable via note_filepath. The two
    are now one commit, so a failure anywhere rolls back to the old path entirely."""
    from kajet_turbo.repositories.git import GitError

    result = service.save("u1", "ws", str(workspace), "Original", "old content", [])
    note_id = result["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    original_bytes = (workspace / "Original.md").read_bytes()

    with (
        patch(
            "kajet_turbo.repositories.git.GitRepository.commit_changes",
            side_effect=GitError("fail"),
        ),
        pytest.raises(GitError, match="fail"),
    ):
        service.update(
            note_id,
            owner_id="u1",
            ws_path=str(workspace),
            expected_sha=sha,
            title="New title",
            content="new content",
        )

    assert (workspace / "Original.md").exists()
    assert (workspace / "Original.md").read_bytes() == original_bytes
    assert not (workspace / "New title.md").exists()
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.title == "Original"
    assert note.content == "old content"


def test_update_db_failure_leaves_file_and_row_untouched(service, workspace):
    """#155: the row write runs before the git commit, so a DB-side failure must abort
    before the tree or HEAD ever change."""

    result = service.save("u1", "ws", str(workspace), "Stable", "old content", [])
    note_id = result["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    flaky_update = make_flaky_db_write(service._crud_repo.update_in_session)

    with (
        patch.object(service._crud_repo, "update_in_session", flaky_update),
        pytest.raises(RuntimeError, match="db exploded"),
    ):
        service.update(
            note_id,
            owner_id="u1",
            ws_path=str(workspace),
            expected_sha=sha,
            content="new content",
        )

    assert head_sha(workspace, "Stable.md") == sha
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.content == "old content"
    assert note.title == "Stable"


def test_update_title_renames_file(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Old title", "content", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="New title"
    )

    assert not (workspace / "Old title.md").exists()
    assert (workspace / "New title.md").exists()


def test_update_rejects_rename_onto_normalization_collision(service, workspace):
    """ "X" renamed to "A:B" would land on "A B.md", already used by "A B"."""
    x_id = service.save("u1", "ws", str(workspace), "X", "x content", [])["note_id"]
    service.save("u1", "ws", str(workspace), "A B", "a b content", [])
    sha = service.get_history(x_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    with pytest.raises(FileExistsError, match="A B"):
        service.update(x_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="A:B")

    x_note = service.get_with_content(x_id, owner_id="u1", ws_path=str(workspace))
    assert x_note.title == "X"
    assert x_note.content == "x content"
    from kajet_turbo.workspace import read_note_file

    _, other_content = read_note_file(str(workspace / "A B.md"))
    assert other_content.strip() == "a b content"


def test_update_rejects_rename_onto_case_only_collision(service, workspace):
    """ "X" renamed to "readme" collides with an existing "Readme" — same file on a
    case-insensitive checkout filesystem (Windows/macOS), even though prod's own
    case-sensitive filesystem would happily keep both."""
    x_id = service.save("u1", "ws", str(workspace), "X", "x content", [])["note_id"]
    service.save("u1", "ws", str(workspace), "Readme", "readme content", [])
    sha = service.get_history(x_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    with pytest.raises(FileExistsError, match="readme"):
        service.update(
            x_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="readme"
        )

    x_note = service.get_with_content(x_id, owner_id="u1", ws_path=str(workspace))
    assert x_note.title == "X"


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
        mode="replace_text",
        old_str="world",
        new_str="earth",
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
        mode="insert_after",
        old_str="- A",
        new_str="- A.5",
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


def test_update_replace_text_requires_new_str(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notatka", "Hello world.", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    with pytest.raises(ValueError, match="requires new_str"):
        service.update(
            note_id,
            owner_id="u1",
            ws_path=str(workspace),
            expected_sha=sha,
            mode="replace_text",
            old_str="world",
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
        old_str="- B\n",
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
            mode="replace_text",
            old_str="foo",
            new_str="qux",
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
        old_str="foo",
        new_str="qux",
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
        old_str="unique",
        new_str="new",
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
