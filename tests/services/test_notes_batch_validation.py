"""Shared validation contract for destructive note batches."""

from pathlib import Path

import pytest

from kajet_turbo.repositories.git import GitRepository


def _head_sha(workspace, relative_path: str) -> str:
    return GitRepository(str(workspace)).file_history(relative_path, limit=1)[0]["sha"]


def _run(operation: str, service, workspace, items: list[dict]) -> dict:
    if operation == "edit":
        edits = [{"mode": "append", "content": "x", **item} for item in items]
        return service.edit_many("u1", "ws", str(workspace), edits)
    return service.delete_many("u1", "ws", str(workspace), items)


@pytest.mark.parametrize("operation", ["edit", "delete"])
@pytest.mark.parametrize(
    ("case", "items", "expected_note_id", "index", "error"),
    [
        (
            "missing_id",
            lambda note_id, sha: [{"note_id": "", "expected_sha": sha}],
            "",
            0,
            "note_id jest wymagany.",
        ),
        (
            "duplicate",
            lambda note_id, sha: [
                {"note_id": note_id, "expected_sha": sha},
                {"note_id": note_id, "expected_sha": sha},
            ],
            "{note_id}",
            1,
            "Duplikat note_id w batchu: '{note_id}'.",
        ),
        (
            "missing_note",
            lambda note_id, sha: [{"note_id": "does-not-exist", "expected_sha": sha}],
            "does-not-exist",
            0,
            "Notatka does-not-exist nie znaleziona.",
        ),
        (
            "missing_sha",
            lambda note_id, sha: [{"note_id": note_id}],
            "{note_id}",
            0,
            "expected_sha jest wymagany.",
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
            "Plik notatki {note_id} nie znaleziony.",
        ),
    ],
)
def test_destructive_batches_share_validation_errors(
    operation, case, items, expected_note_id, index, error, service, workspace
):
    saved = service.save("u1", "ws", str(workspace), "First", "one\n", [])
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
        assert service.get_with_content(note_id, "u1", str(workspace)) is not None


def test_edit_many_preserves_mixed_validation_error_order(service, workspace):
    first = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    second = service.save("u1", "ws", str(workspace), "Second", "two\n", [])

    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            {
                "note_id": first["note_id"],
                "mode": "replace_text",
                "old_str": "missing",
                "new_str": "x",
                "expected_sha": _head_sha(workspace, "First.md"),
            },
            {"note_id": second["note_id"]},
        ],
    )

    assert [error["index"] for error in result["errors"]] == [0, 1]
