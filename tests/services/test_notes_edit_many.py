"""edit_many() batch coverage for NoteService."""

from unittest.mock import patch

import pytest

from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.services.notes import service as service_module


def test_edit_many_applies_all_in_one_commit(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", [])
    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            {
                "note_id": r1["note_id"],
                "mode": "append",
                "content": "more",
                "expected_sha": _head_sha(workspace, "First.md"),
            },
            {
                "note_id": r2["note_id"],
                "mode": "append",
                "content": "more",
                "expected_sha": _head_sha(workspace, "Second.md"),
            },
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
            {
                "note_id": r1["note_id"],
                "mode": "append",
                "content": "more",
                "expected_sha": _head_sha(workspace, "First.md"),
            },
            {
                "note_id": r2["note_id"],
                "mode": "replace_text",
                "old_str": "does-not-exist",
                "new_str": "x",
                "expected_sha": _head_sha(workspace, "Second.md"),
            },
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
            {
                "note_id": r1["note_id"],
                "mode": "append",
                "content": "a",
                "expected_sha": _head_sha(workspace, "First.md"),
            },
            {"note_id": r1["note_id"], "mode": "append", "content": "b"},
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
            {
                "note_id": r1["note_id"],
                "mode": "append",
                "content": "x",
                "expected_sha": _head_sha(workspace, "First.md"),
            },
            {"note_id": "does-not-exist", "mode": "append", "content": "y"},
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
            {
                "note_id": r1["note_id"],
                "mode": "overwrite",
                "content": "replaced",
                "expected_sha": _head_sha(workspace, "First.md"),
            }
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
            {
                "note_id": r1["note_id"],
                "mode": "replace_text",
                "old_str": "foo",
                "new_str": "bar",
                "replace_all": True,
                "expected_sha": _head_sha(workspace, "First.md"),
            }
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
        [
            {
                "note_id": r1["note_id"],
                "mode": "append",
                "content": "x",
                "tags": ["new"],
                "expected_sha": _head_sha(workspace, "First.md"),
            }
        ],
    )
    assert result["applied"] is True
    note1 = service.get_with_content(r1["note_id"], "u1", str(workspace))
    assert note1.tags == ["new"]


def test_edit_many_empty_batch_raises(service, workspace):
    with pytest.raises(ValueError):
        service.edit_many("u1", "ws", str(workspace), [])


def test_edit_many_git_error_rolls_back_all_files(service, workspace):
    # Mirrors test_save_many_git_error_rolls_back_all_files: commit_files failing after
    # files are written must restore every file, not just some.
    from kajet_turbo.repositories.git import GitError

    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", [])
    with (
        patch(
            "kajet_turbo.repositories.git.GitRepository.commit_files",
            side_effect=GitError("fail"),
        ),
        pytest.raises(GitError),
    ):
        service.edit_many(
            "u1",
            "ws",
            str(workspace),
            [
                {
                    "note_id": r1["note_id"],
                    "mode": "append",
                    "content": "more",
                    "expected_sha": _head_sha(workspace, "First.md"),
                },
                {
                    "note_id": r2["note_id"],
                    "mode": "append",
                    "content": "more",
                    "expected_sha": _head_sha(workspace, "Second.md"),
                },
            ],
        )

    note1 = service.get_with_content(r1["note_id"], "u1", str(workspace))
    note2 = service.get_with_content(r2["note_id"], "u1", str(workspace))
    assert note1.content == "one"
    assert note2.content == "two"


def test_edit_many_write_failing_partway_rolls_back_and_makes_no_commit(service, workspace):
    """The literal #104 acceptance test: an OSError from write_note_file itself (not just
    a commit_files failure after every file already landed) must still leave no file
    written and no commit made — mirrors
    test_rename_tag_restores_every_touched_file_when_a_write_fails."""
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", [])
    head_before = _head_sha(workspace, "First.md")

    real_write = service_module.write_note_file
    calls = {"n": 0}

    def flaky_write(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real_write(*args, **kwargs)

    with (
        patch.object(service_module, "write_note_file", flaky_write),
        pytest.raises(OSError, match="disk full"),
    ):
        service.edit_many(
            "u1",
            "ws",
            str(workspace),
            [
                {
                    "note_id": r1["note_id"],
                    "mode": "append",
                    "content": "more",
                    "expected_sha": _head_sha(workspace, "First.md"),
                },
                {
                    "note_id": r2["note_id"],
                    "mode": "append",
                    "content": "more",
                    "expected_sha": _head_sha(workspace, "Second.md"),
                },
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
        [
            {
                "note_id": r1["note_id"],
                "mode": "append",
                "content": "bump",
                "expected_sha": stale_sha,
            }
        ],
    )

    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            {
                "note_id": r1["note_id"],
                "mode": "append",
                "content": "more",
                "expected_sha": stale_sha,
            },
            {
                "note_id": r2["note_id"],
                "mode": "append",
                "content": "more",
                "expected_sha": _head_sha(workspace, "Second.md"),
            },
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
        [{"note_id": r1["note_id"], "mode": "append", "content": "more"}],
    )
    assert result["applied"] is False
    assert "wymagany" in result["errors"][0]["error"]


def _head_sha(workspace, relative_path):
    return GitRepository(str(workspace)).file_history(relative_path, limit=1)[0]["sha"]
