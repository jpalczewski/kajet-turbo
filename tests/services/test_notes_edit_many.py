"""edit_many() batch coverage for NoteService."""

from unittest.mock import patch

import pytest

from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.services.notes import service as service_module
from tests.services.conftest import seed_user
from tests.services.helpers import edit_item, make_flaky_db_write, make_flaky_write


@pytest.fixture(autouse=True)
def _seed_default_owner(database):
    # edit_many now enqueues reindex_note jobs (user_id FK to users.id).
    seed_user(database, "u1")


def test_edit_many_applies_all_in_one_commit(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", [])
    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            edit_item(r1["note_id"], _head_sha(workspace, "First.md"), content="more"),
            edit_item(r2["note_id"], _head_sha(workspace, "Second.md"), content="more"),
        ],
    )
    assert result["applied"] is True
    assert [r["note_id"] for r in result["results"]] == [r1["note_id"], r2["note_id"]]
    note1 = service.get_with_content(r1["note_id"], "u1", str(workspace))
    note2 = service.get_with_content(r2["note_id"], "u1", str(workspace))
    assert "more" in note1.content
    assert "more" in note2.content
    history = GitRepository(str(workspace)).file_history("First.md")
    assert len(history) == 2  # save + one batch-edit commit, not split per note
    assert history[0]["message"].startswith("note: edit 2 notes")


def test_edit_many_all_or_nothing_on_bad_anchor(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", [])
    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            edit_item(r1["note_id"], _head_sha(workspace, "First.md"), content="more"),
            edit_item(
                r2["note_id"],
                _head_sha(workspace, "Second.md"),
                mode="replace_text",
                old_str="does-not-exist",
                new_str="x",
            ),
        ],
    )
    assert result["applied"] is False
    assert result["errors"][0]["index"] == 1
    note1 = service.get_with_content(r1["note_id"], "u1", str(workspace))
    assert note1.content == "one"  # nothing written, including the valid first item


def test_edit_many_rejects_duplicate_note_id(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            edit_item(r1["note_id"], _head_sha(workspace, "First.md"), content="a"),
            edit_item(r1["note_id"], content="b"),
        ],
    )
    assert result["applied"] is False
    assert "Duplikat" in result["errors"][0]["error"]


def test_edit_many_missing_note_rejects_batch(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            edit_item(r1["note_id"], _head_sha(workspace, "First.md"), content="x"),
            edit_item("does-not-exist", content="y"),
        ],
    )
    assert result["applied"] is False


def test_edit_many_applies_destructive_overwrite_with_fresh_sha(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "existing content\n", [])
    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            edit_item(
                r1["note_id"],
                _head_sha(workspace, "First.md"),
                mode="overwrite",
                content="replaced",
            )
        ],
    )
    assert result["applied"] is True
    note1 = service.get_with_content(r1["note_id"], "u1", str(workspace))
    assert note1.content == "replaced"


def test_edit_many_replace_all_reports_count_per_item(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "foo foo foo\n", [])
    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            edit_item(
                r1["note_id"],
                _head_sha(workspace, "First.md"),
                mode="replace_text",
                old_str="foo",
                new_str="bar",
                replace_all=True,
            )
        ],
    )
    assert result["applied"] is True
    assert result["results"][0]["replaced"] == 3


def test_edit_many_updates_tags(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "body\n", ["old"])
    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [edit_item(r1["note_id"], _head_sha(workspace, "First.md"), content="x", tags=["new"])],
    )
    assert result["applied"] is True
    note1 = service.get_with_content(r1["note_id"], "u1", str(workspace))
    assert note1.tags == ["new"]


def test_edit_many_empty_batch_raises(service, workspace):
    with pytest.raises(ValueError):
        service.edit_many("u1", "ws", str(workspace), [])


def test_edit_many_git_error_rolls_back_all_files(service, workspace):
    # Mirrors test_save_many_git_error_rolls_back_all_files: commit_changes failing after
    # files are written must restore every file, not just some.
    from kajet_turbo.repositories.git import GitError

    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", [])
    with (
        patch(
            "kajet_turbo.repositories.git.GitRepository.commit_changes",
            side_effect=GitError("fail"),
        ),
        pytest.raises(GitError),
    ):
        service.edit_many(
            "u1",
            "ws",
            str(workspace),
            [
                edit_item(r1["note_id"], _head_sha(workspace, "First.md"), content="more"),
                edit_item(r2["note_id"], _head_sha(workspace, "Second.md"), content="more"),
            ],
        )

    note1 = service.get_with_content(r1["note_id"], "u1", str(workspace))
    note2 = service.get_with_content(r2["note_id"], "u1", str(workspace))
    assert note1.content == "one"
    assert note2.content == "two"
    # #155: rows are updated before the git commit inside one transaction for the batch,
    # so a git-side failure must roll the already-flushed bump back too, not just the file.
    row1 = service._crud_repo.get(r1["note_id"], owner_id="u1")
    row2 = service._crud_repo.get(r2["note_id"], owner_id="u1")
    assert row1.index_generation == 1
    assert row2.index_generation == 1


def test_edit_many_write_failing_partway_rolls_back_and_makes_no_commit(service, workspace):
    """The literal #104 acceptance test: an OSError from write_note_file itself (not just
    a commit_changes failure after every file already landed) must still leave no file
    written and no commit made — mirrors
    test_rename_tag_restores_every_touched_file_when_a_write_fails."""
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", [])
    head_before = _head_sha(workspace, "First.md")

    flaky_write = make_flaky_write(service_module.write_note_file)

    with (
        patch.object(service_module, "write_note_file", flaky_write),
        pytest.raises(OSError, match="disk full"),
    ):
        service.edit_many(
            "u1",
            "ws",
            str(workspace),
            [
                edit_item(r1["note_id"], _head_sha(workspace, "First.md"), content="more"),
                edit_item(r2["note_id"], _head_sha(workspace, "Second.md"), content="more"),
            ],
        )

    note1 = service.get_with_content(r1["note_id"], "u1", str(workspace))
    note2 = service.get_with_content(r2["note_id"], "u1", str(workspace))
    assert note1.content == "one"
    assert note2.content == "two"
    assert _head_sha(workspace, "First.md") == head_before


def test_edit_many_db_failure_leaves_files_and_head_untouched(service, workspace):
    """#155: rows are written before the tree, in one transaction for the whole batch, so
    a DB-side failure on any item must abort before the tree or HEAD change for any of
    them — not just the item that failed."""
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", [])
    head_before = _head_sha(workspace, "First.md")

    flaky_update = make_flaky_db_write(service._crud_repo.update_in_session, fail_on_call=2)

    with (
        patch.object(service._crud_repo, "update_in_session", flaky_update),
        pytest.raises(RuntimeError, match="db exploded"),
    ):
        service.edit_many(
            "u1",
            "ws",
            str(workspace),
            [
                edit_item(r1["note_id"], _head_sha(workspace, "First.md"), content="more"),
                edit_item(r2["note_id"], _head_sha(workspace, "Second.md"), content="more"),
            ],
        )

    note1 = service.get_with_content(r1["note_id"], "u1", str(workspace))
    note2 = service.get_with_content(r2["note_id"], "u1", str(workspace))
    assert note1.content == "one"
    assert note2.content == "two"
    assert _head_sha(workspace, "First.md") == head_before


def test_edit_many_stale_sha_rejects_whole_batch(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", [])
    stale_sha = _head_sha(workspace, "First.md")
    service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [edit_item(r1["note_id"], stale_sha, content="bump")],
    )

    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            edit_item(r1["note_id"], stale_sha, content="more"),
            edit_item(r2["note_id"], _head_sha(workspace, "Second.md"), content="more"),
        ],
    )

    assert result["applied"] is False
    assert "current_sha" not in result["errors"][0]
    note1 = service.get_with_content(r1["note_id"], "u1", str(workspace))
    note2 = service.get_with_content(r2["note_id"], "u1", str(workspace))
    assert "more" not in note1.content
    assert "more" not in note2.content  # nothing written, including the valid second item


def test_edit_many_requires_expected_sha(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [edit_item(r1["note_id"], content="more")],
    )
    assert result["applied"] is False
    assert "wymagany" in result["errors"][0]["error"]


def _head_sha(workspace, relative_path):
    return GitRepository(str(workspace)).file_history(relative_path, limit=1)[0]["sha"]
