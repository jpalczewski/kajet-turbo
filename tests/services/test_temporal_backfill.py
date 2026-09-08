from unittest.mock import patch

import pytest

from kajet_turbo.services.notes.temporal import BackfillStaleError, _classify_temporal_note
from kajet_turbo.workspace import read_note_file
from tests.services.conftest import workspace_target
from tests.services.helpers import head_sha, make_flaky_db_write, rel_path


@pytest.mark.parametrize(
    ("title", "folder", "expected_kind", "expected_reason"),
    [
        # A whole-title year stands on its own, undated folder or not (#143's yearly
        # note convention: a title that IS the token is what backfill exists to catch).
        ("2026", "", "candidate", None),
        ("2026", "journal/yearly", "candidate", None),
        ("2026", "archive/2026", "candidate", None),
        # A bare year alongside other words has no structure distinguishing it from an
        # invoice/room/version number, and an undated folder never corroborates it.
        ("Invoice 2026", "", "ambiguous", "bare year in title has no corroborating folder date"),
        ("Invoice 2026", "archive/2026", "candidate", None),
        ("Invoice 2026", "archive/2025", "ambiguous", "folder date conflicts with title"),
        # Non-year grains are unaffected: they already carry month/day structure.
        ("2026-03-22 Daily", "", "candidate", None),
        ("2026-03 Report", "", "candidate", None),
    ],
)
def test_classify_temporal_note_bare_year_corroboration(
    title, folder, expected_kind, expected_reason
):
    kind, payload = _classify_temporal_note("n1", title, folder, None, None)

    assert kind == expected_kind
    if expected_reason is not None:
        assert payload["reason"] == expected_reason


def test_temporal_backfill_updates_metadata_without_bumping_index(
    service, temporal_service, workspace
):
    note_id = service.save(workspace_target("u1", "ws", workspace), "2026-03-22 Daily", "body", [])[
        "note_id"
    ]
    before = service._crud_repo.get(note_id, owner_id="u1")
    assert before is not None
    preview = temporal_service.temporal_backfill_preview("ws", "u1", str(workspace))
    assert preview["candidates"][0]["field"] == "occurred_at"

    result = temporal_service.apply_temporal_backfill(
        "ws", "u1", str(workspace), preview["candidates"]
    )

    after = service._crud_repo.get(note_id, owner_id="u1")
    assert result == {"applied": 1}
    assert after is not None and after.occurred_at == "2026-03-22"
    assert after.index_generation == before.index_generation
    meta, body = read_note_file(str(workspace / "2026-03-22 Daily.md"))
    assert (meta.occurred_at, body) == ("2026-03-22", "body")


def test_temporal_backfill_db_failure_leaves_file_row_and_head_untouched(
    service, temporal_service, workspace
):
    """#155: rows are written before the tree, so a DB-side failure must abort before the
    frontmatter rewrite or the git commit ever happen."""
    note_id = service.save(workspace_target("u1", "ws", workspace), "2026-03-22 Daily", "body", [])[
        "note_id"
    ]
    preview = temporal_service.temporal_backfill_preview("ws", "u1", str(workspace))
    before = service._crud_repo.get(note_id, owner_id="u1")
    assert before is not None and before.occurred_at is None
    head_before = head_sha(workspace, "2026-03-22 Daily.md")

    flaky_update = make_flaky_db_write(service._crud_repo.update_in_session)

    with (
        patch.object(service._crud_repo, "update_in_session", flaky_update),
        pytest.raises(RuntimeError, match="db exploded"),
    ):
        temporal_service.apply_temporal_backfill("ws", "u1", str(workspace), preview["candidates"])

    after = service._crud_repo.get(note_id, owner_id="u1")
    assert after is not None and after.occurred_at is None
    assert head_sha(workspace, "2026-03-22 Daily.md") == head_before
    meta, body = read_note_file(str(workspace / "2026-03-22 Daily.md"))
    assert (meta.occurred_at, body) == (None, "body")


def test_temporal_backfill_git_error_rolls_back_row_and_file(service, temporal_service, workspace):
    """#155: the row update is flushed before the git commit inside the same
    transaction, so a git-side failure must roll the already-flushed row back too, not
    just leave the frontmatter untouched."""
    from kajet_turbo.repositories.git import GitError

    note_id = service.save(workspace_target("u1", "ws", workspace), "2026-03-22 Daily", "body", [])[
        "note_id"
    ]
    preview = temporal_service.temporal_backfill_preview("ws", "u1", str(workspace))
    head_before = head_sha(workspace, "2026-03-22 Daily.md")

    with (
        patch(
            "kajet_turbo.repositories.git.GitRepository.commit_changes",
            side_effect=GitError("fail"),
        ),
        pytest.raises(GitError),
    ):
        temporal_service.apply_temporal_backfill("ws", "u1", str(workspace), preview["candidates"])

    after = service._crud_repo.get(note_id, owner_id="u1")
    assert after is not None and after.occurred_at is None
    assert head_sha(workspace, "2026-03-22 Daily.md") == head_before
    meta, body = read_note_file(str(workspace / "2026-03-22 Daily.md"))
    assert (meta.occurred_at, body) == (None, "body")


def test_temporal_backfill_reports_uncorroborated_bare_year(service, temporal_service, workspace):
    """#143: a bare year alongside other words (an invoice/room/version number that
    happens to look like a year) must not become a silently bulk-applicable candidate
    just because its folder is undated — undated means "no conflict", not "corroborated"."""
    service.save(workspace_target("u1", "ws", workspace), "Invoice 2026", "body", [])
    preview = temporal_service.temporal_backfill_preview("ws", "u1", str(workspace))
    assert preview["candidates"] == []
    assert (
        preview["ambiguous"][0]["reason"] == "bare year in title has no corroborating folder date"
    )


def test_temporal_backfill_rejects_uncorroborated_bare_year_candidate(
    service, temporal_service, workspace
):
    """A hand-crafted candidate for an uncorroborated bare year must be refused by
    apply, not just filtered out of preview — the server-side re-classify in
    apply_temporal_backfill is the actual enforcement point, not the preview list.

    A genuine candidate is saved alongside it and its preview sha is cross-checked
    against the same head_sha() helper used to forge the bare-year candidate's sha —
    proving the helper matches what locate_many's freshness check expects, so the
    raise below can only come from the re-classify check, not a coincidentally stale
    sha for an unrelated reason.
    """
    note_id = service.save(workspace_target("u1", "ws", workspace), "Invoice 2026", "body", [])[
        "note_id"
    ]
    service.save(workspace_target("u1", "ws", workspace), "2026-03-22 Daily", "body", [])

    preview = temporal_service.temporal_backfill_preview("ws", "u1", str(workspace))
    assert preview["ambiguous"][0]["note_id"] == note_id
    assert preview["candidates"][0]["sha"] == head_sha(workspace, "2026-03-22 Daily.md")

    forged_candidate = {
        "note_id": note_id,
        "title": "Invoice 2026",
        "folder": "",
        "field": "period",
        "value": "2026",
        "sha": head_sha(workspace, "Invoice 2026.md"),
    }

    with pytest.raises(BackfillStaleError):
        temporal_service.apply_temporal_backfill("ws", "u1", str(workspace), [forged_candidate])

    after = service._crud_repo.get(note_id, owner_id="u1")
    assert after is not None and after.period is None
    meta, body = read_note_file(str(workspace / "Invoice 2026.md"))
    assert (meta.period, body) == (None, "body")


def test_temporal_backfill_reports_conflicting_folder(service, temporal_service, workspace):
    service.save(
        workspace_target("u1", "ws", workspace),
        "2026-03-22",
        "body",
        [],
        folder="journal/2026/04",
    )
    preview = temporal_service.temporal_backfill_preview("ws", "u1", str(workspace))
    assert preview["candidates"] == []
    assert preview["ambiguous"][0]["reason"] == "folder date conflicts with title"


def test_temporal_backfill_reports_conflicting_week_folder(service, temporal_service, workspace):
    # ISO week 2026-W12 falls in March (month_of_week), so a folder claiming April
    # is a genuine conflict a day/month-only check would miss for week-grain titles.
    service.save(
        workspace_target("u1", "ws", workspace),
        "2026-W12 Weekly Review",
        "body",
        [],
        folder="weekly/2026/04",
    )
    preview = temporal_service.temporal_backfill_preview("ws", "u1", str(workspace))
    assert preview["candidates"] == []
    assert preview["ambiguous"][0]["reason"] == "folder date conflicts with title"


def test_temporal_backfill_applies_note_with_no_git_history(
    service, reconcile_service, temporal_service, workspace, note_file_factory
):
    # A file reconciled onto disk (e.g. pre-existing data) has no commit touching it yet,
    # so its preview candidate carries sha=None; apply must still accept it as fresh.
    path = note_file_factory(workspace, "2026-03-22 Daily", note_id="nogit1", content="body")
    reconcile_service.reconcile_paths(
        "ws", owner_id="u1", ws_path=str(workspace), paths=[rel_path(workspace, path)]
    )

    preview = temporal_service.temporal_backfill_preview("ws", "u1", str(workspace))
    assert preview["candidates"][0]["sha"] is None

    result = temporal_service.apply_temporal_backfill(
        "ws", "u1", str(workspace), preview["candidates"]
    )

    assert result == {"applied": 1}
    after = service._crud_repo.get("nogit1", owner_id="u1")
    assert after is not None and after.occurred_at == "2026-03-22"


def test_temporal_backfill_rejects_malformed_note_id_without_writing(
    service, temporal_service, workspace
):
    note_id = service.save(workspace_target("u1", "ws", workspace), "2026-03-22 Daily", "body", [])[
        "note_id"
    ]
    preview = temporal_service.temporal_backfill_preview("ws", "u1", str(workspace))
    candidate = {**preview["candidates"][0], "note_id": None}

    with pytest.raises(ValueError, match="note_id"):
        temporal_service.apply_temporal_backfill("ws", "u1", str(workspace), [candidate])

    after = service._crud_repo.get(note_id, owner_id="u1")
    assert after is not None and after.occurred_at is None
