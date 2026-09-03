from pathlib import Path
from unittest.mock import patch

import pytest

from kajet_turbo.workspace import read_note_file
from tests.services.helpers import head_sha, make_flaky_db_write


def _rel(ws_path, filepath: str) -> str:
    return str(Path(filepath).relative_to(ws_path))


def test_temporal_backfill_updates_metadata_without_bumping_index(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "2026-03-22 Daily", "body", [])["note_id"]
    before = service._crud_repo.get(note_id, owner_id="u1")
    assert before is not None
    preview = service.temporal_backfill_preview("ws", "u1", str(workspace))
    assert preview["candidates"][0]["field"] == "occurred_at"

    result = service.apply_temporal_backfill("ws", "u1", str(workspace), preview["candidates"])

    after = service._crud_repo.get(note_id, owner_id="u1")
    assert result == {"applied": 1}
    assert after is not None and after.occurred_at == "2026-03-22"
    assert after.index_generation == before.index_generation
    meta, body = read_note_file(str(workspace / "2026-03-22 Daily.md"))
    assert (meta.occurred_at, body) == ("2026-03-22", "body")


def test_temporal_backfill_db_failure_leaves_file_row_and_head_untouched(service, workspace):
    """#155: rows are written before the tree, so a DB-side failure must abort before the
    frontmatter rewrite or the git commit ever happen."""
    note_id = service.save("u1", "ws", str(workspace), "2026-03-22 Daily", "body", [])["note_id"]
    preview = service.temporal_backfill_preview("ws", "u1", str(workspace))
    before = service._crud_repo.get(note_id, owner_id="u1")
    assert before is not None and before.occurred_at is None
    head_before = head_sha(workspace, "2026-03-22 Daily.md")

    flaky_update = make_flaky_db_write(service._crud_repo.update_in_session)

    with (
        patch.object(service._crud_repo, "update_in_session", flaky_update),
        pytest.raises(RuntimeError, match="db exploded"),
    ):
        service.apply_temporal_backfill("ws", "u1", str(workspace), preview["candidates"])

    after = service._crud_repo.get(note_id, owner_id="u1")
    assert after is not None and after.occurred_at is None
    assert head_sha(workspace, "2026-03-22 Daily.md") == head_before
    meta, body = read_note_file(str(workspace / "2026-03-22 Daily.md"))
    assert (meta.occurred_at, body) == (None, "body")


def test_temporal_backfill_git_error_rolls_back_row_and_file(service, workspace):
    """#155: the row update is flushed before the git commit inside the same
    transaction, so a git-side failure must roll the already-flushed row back too, not
    just leave the frontmatter untouched."""
    from kajet_turbo.repositories.git import GitError

    note_id = service.save("u1", "ws", str(workspace), "2026-03-22 Daily", "body", [])["note_id"]
    preview = service.temporal_backfill_preview("ws", "u1", str(workspace))
    head_before = head_sha(workspace, "2026-03-22 Daily.md")

    with (
        patch(
            "kajet_turbo.repositories.git.GitRepository.commit_changes",
            side_effect=GitError("fail"),
        ),
        pytest.raises(GitError),
    ):
        service.apply_temporal_backfill("ws", "u1", str(workspace), preview["candidates"])

    after = service._crud_repo.get(note_id, owner_id="u1")
    assert after is not None and after.occurred_at is None
    assert head_sha(workspace, "2026-03-22 Daily.md") == head_before
    meta, body = read_note_file(str(workspace / "2026-03-22 Daily.md"))
    assert (meta.occurred_at, body) == (None, "body")


def test_temporal_backfill_reports_conflicting_folder(service, workspace):
    service.save("u1", "ws", str(workspace), "2026-03-22", "body", [], folder="journal/2026/04")
    preview = service.temporal_backfill_preview("ws", "u1", str(workspace))
    assert preview["candidates"] == []
    assert preview["ambiguous"][0]["reason"] == "folder date conflicts with title"


def test_temporal_backfill_reports_conflicting_week_folder(service, workspace):
    # ISO week 2026-W12 falls in March (month_of_week), so a folder claiming April
    # is a genuine conflict a day/month-only check would miss for week-grain titles.
    service.save(
        "u1", "ws", str(workspace), "2026-W12 Weekly Review", "body", [], folder="weekly/2026/04"
    )
    preview = service.temporal_backfill_preview("ws", "u1", str(workspace))
    assert preview["candidates"] == []
    assert preview["ambiguous"][0]["reason"] == "folder date conflicts with title"


def test_temporal_backfill_applies_note_with_no_git_history(service, workspace, note_file_factory):
    # A file reconciled onto disk (e.g. pre-existing data) has no commit touching it yet,
    # so its preview candidate carries sha=None; apply must still accept it as fresh.
    path = note_file_factory(workspace, "2026-03-22 Daily", note_id="nogit1", content="body")
    service.reconcile_paths(
        "ws", owner_id="u1", ws_path=str(workspace), paths=[_rel(workspace, path)]
    )

    preview = service.temporal_backfill_preview("ws", "u1", str(workspace))
    assert preview["candidates"][0]["sha"] is None

    result = service.apply_temporal_backfill("ws", "u1", str(workspace), preview["candidates"])

    assert result == {"applied": 1}
    after = service._crud_repo.get("nogit1", owner_id="u1")
    assert after is not None and after.occurred_at == "2026-03-22"


def test_temporal_backfill_rejects_malformed_note_id_without_writing(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "2026-03-22 Daily", "body", [])["note_id"]
    preview = service.temporal_backfill_preview("ws", "u1", str(workspace))
    candidate = {**preview["candidates"][0], "note_id": None}

    with pytest.raises(ValueError, match="note_id"):
        service.apply_temporal_backfill("ws", "u1", str(workspace), [candidate])

    after = service._crud_repo.get(note_id, owner_id="u1")
    assert after is not None and after.occurred_at is None
