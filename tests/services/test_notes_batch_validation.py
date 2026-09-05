"""Shared validation contract for destructive note batches."""

from pathlib import Path

import pytest

from kajet_turbo.repositories.git import GitRepository
from tests.services.conftest import note_target, workspace_target
from tests.services.helpers import edit_item


def _head_sha(workspace, relative_path: str) -> str:
    return GitRepository(str(workspace)).file_history(relative_path, limit=1)[0]["sha"]


def _run(operation: str, service, workspace, items: list[dict]) -> dict:
    if operation == "edit":
        edits = [
            edit_item(item.get("note_id", ""), item.get("expected_sha", ""), content="x")
            for item in items
        ]
        return service.edit_many(workspace_target("u1", "ws", workspace), edits)
    return service.delete_many(workspace_target("u1", "ws", workspace), items)


@pytest.mark.parametrize("operation", ["edit", "delete"])
@pytest.mark.parametrize(
    ("case", "items", "expected_note_id", "index", "error"),
    [
        (
            "missing_id",
            lambda note_id, sha: [{"note_id": "", "expected_sha": sha}],
            "",
            0,
            "note_id is required.",
        ),
        (
            "duplicate",
            lambda note_id, sha: [
                {"note_id": note_id, "expected_sha": sha},
                {"note_id": note_id, "expected_sha": sha},
            ],
            "{note_id}",
            1,
            "Duplicate note_id in batch: '{note_id}'.",
        ),
        (
            "missing_note",
            lambda note_id, sha: [{"note_id": "does-not-exist", "expected_sha": sha}],
            "does-not-exist",
            0,
            "Note not found: note_id=does-not-exist",
        ),
        (
            "missing_sha",
            lambda note_id, sha: [{"note_id": note_id}],
            "{note_id}",
            0,
            "expected_sha is required.",
        ),
        (
            "stale_sha",
            lambda note_id, sha: [{"note_id": note_id, "expected_sha": "0" * 40}],
            "{note_id}",
            0,
            (
                "expected_sha nieaktualny dla {note_id}. Wywołaj get_note, "
                "by pobrać aktualną treść przed ponowną edycją."
            ),
        ),
        (
            "missing_file",
            lambda note_id, sha: [{"note_id": note_id, "expected_sha": sha}],
            "{note_id}",
            0,
            "Note file not found: note_id={note_id}",
        ),
    ],
)
def test_destructive_batches_share_validation_errors(
    operation, case, items, expected_note_id, index, error, service, workspace
):
    saved = service.save(workspace_target("u1", "ws", workspace), "First", "one\n", [])
    note_id = saved["note_id"]
    sha = _head_sha(workspace, "First.md")
    if case == "missing_file":
        Path(workspace, "First.md").unlink()

    result = _run(operation, service, workspace, items(note_id, sha))

    assert result == {
        "applied": False,
        "errors": [
            {
                "index": index,
                "note_id": expected_note_id.format(note_id=note_id),
                "error": error.format(note_id=note_id),
            }
        ],
    }
    if case != "missing_file":
        assert service.get_with_content(note_target("u1", "ws", workspace, note_id)) is not None


def test_edit_many_preserves_mixed_validation_error_order(service, workspace):
    first = service.save(workspace_target("u1", "ws", workspace), "First", "one\n", [])
    second = service.save(workspace_target("u1", "ws", workspace), "Second", "two\n", [])

    result = service.edit_many(
        workspace_target("u1", "ws", workspace),
        [
            edit_item(
                first["note_id"],
                _head_sha(workspace, "First.md"),
                mode="replace_text",
                old_str="missing",
                new_str="x",
            ),
            edit_item(second["note_id"]),
        ],
    )

    assert [error["index"] for error in result["errors"]] == [0, 1]
