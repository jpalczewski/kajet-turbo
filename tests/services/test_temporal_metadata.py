from dataclasses import replace

import frontmatter
import pytest

from kajet_turbo.workspace import (
    TemporalMetadataError,
    note_filepath,
    parse_frontmatter,
    read_note_file,
    write_note_file,
)
from tests.services.helpers import corrupt_temporal_field


def test_parse_frontmatter_tolerates_malformed_occurred_at():
    """A hand-edited/corrupted occurred_at must not block reading the rest of the note
    (#132) — it degrades to None instead of raising TemporalMetadataError."""
    post = frontmatter.Post("Body", occurred_at="not-a-date")

    meta, content = parse_frontmatter(post)

    assert meta.occurred_at is None
    assert content == "Body"


def test_parse_frontmatter_tolerates_malformed_period():
    post = frontmatter.Post("Body", period="not-a-period")

    meta, content = parse_frontmatter(post)

    assert meta.period is None
    assert content == "Body"


def test_parse_frontmatter_still_rejects_malformed_values_on_explicit_write(service, workspace):
    """The lenient read path must not weaken explicit-write validation: passing a bad
    value through the service API still raises loudly."""
    note_id = service.save("u1", "ws", str(workspace), "Strict Write", "Body", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    with pytest.raises(TemporalMetadataError):
        service.update(
            note_id,
            owner_id="u1",
            ws_path=str(workspace),
            expected_sha=sha,
            occurred_at="not-a-date",
        )


def test_parse_frontmatter_tolerates_conflicting_but_individually_valid_values():
    """Each of occurred_at/period can parse fine on its own, and yet the file has both
    set — a corruption unreachable through the app's own writes (NoteFrontmatter.__post_init__
    rejects it), but still a hand-edit away. It must degrade like any other corrupted
    value instead of raising past parse_frontmatter (#132 follow-up)."""
    post = frontmatter.Post("Body", occurred_at="2026-03-22", period="2026-W12")

    meta, content = parse_frontmatter(post)

    assert (meta.occurred_at, meta.period) == (None, None)
    assert meta.temporal_dropped == {"occurred_at", "period"}
    assert content == "Body"


def test_update_keeps_db_occurred_at_when_file_value_is_corrupted(service, workspace):
    """An edit unrelated to dates (here: the title) must not silently discard a note's
    occurred_at just because a hand-edit made the on-disk copy unparseable — it should
    fall back to the DB's last-known-good value instead of persisting the drop, and
    surface the drop to the caller (#132 follow-up to the parse_frontmatter leniency fix)."""
    note_id = service.save(
        "u1", "ws", str(workspace), "Corrupt Update", "Body", [], occurred_at="2026-03-22"
    )["note_id"]
    path = note_filepath(str(workspace), "", "Corrupt Update")
    corrupt_temporal_field(path, "occurred_at", "banana")

    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    result = service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="Renamed"
    )

    assert result["temporal_warnings"] == [
        {"kind": "temporal_value_ignored", "field": "occurred_at"}
    ]
    row = service._crud_repo.get(note_id, owner_id="u1")
    assert row is not None and row.occurred_at == "2026-03-22"
    meta, _ = read_note_file(note_filepath(str(workspace), "", "Renamed"))
    assert meta.occurred_at == "2026-03-22"


def test_edit_many_keeps_db_occurred_at_when_file_value_is_corrupted(service, workspace):
    """edit_many's per-item fallback must fall back to the DB's last-known-good value for
    a field read_note_file had to drop, same as update() (#132 follow-up), and surface
    the drop per item rather than only in a server-side log."""
    note_id = service.save(
        "u1", "ws", str(workspace), "Corrupt Batch", "Body", [], occurred_at="2026-03-22"
    )["note_id"]
    path = note_filepath(str(workspace), "", "Corrupt Batch")
    corrupt_temporal_field(path, "occurred_at", "banana")
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [{"note_id": note_id, "mode": "append", "content": "more", "expected_sha": sha}],
    )

    assert result["applied"] is True
    assert result["results"][0]["temporal_warnings"] == [
        {"kind": "temporal_value_ignored", "field": "occurred_at"}
    ]
    row = service._crud_repo.get(note_id, owner_id="u1")
    assert row is not None and row.occurred_at == "2026-03-22"


def test_reconcile_paths_keeps_db_occurred_at_when_file_value_is_corrupted(service, workspace):
    """reconcile_paths must not treat a corrupted (unparseable) on-disk occurred_at as a
    genuine drift-to-None and overwrite the DB's correct value with it (#132 follow-up)."""
    note_id = service.save(
        "u1", "ws", str(workspace), "Corrupt Reconcile", "Body", [], occurred_at="2026-03-22"
    )["note_id"]
    path = note_filepath(str(workspace), "", "Corrupt Reconcile")
    corrupt_temporal_field(path, "occurred_at", "banana")

    service.reconcile_paths(
        "ws", owner_id="u1", ws_path=str(workspace), paths=["Corrupt Reconcile.md"]
    )

    row = service._crud_repo.get(note_id, owner_id="u1")
    assert row is not None and row.occurred_at == "2026-03-22"


def test_save_update_clear_and_reconcile_temporal_metadata(service, workspace):
    note_id = service.save(
        "u1",
        "ws",
        str(workspace),
        "Event",
        "Body",
        [],
        occurred_at="2026-03-22",
    )["note_id"]
    row = service._crud_repo.get(note_id, owner_id="u1")
    assert row is not None and (row.occurred_at, row.period) == ("2026-03-22", None)

    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        period="2026-W12",
    )
    row = service._crud_repo.get(note_id, owner_id="u1")
    assert row is not None and (row.occurred_at, row.period) == (None, "2026-W12")
    meta, _ = read_note_file(note_filepath(str(workspace), "", "Event"))
    assert (meta.occurred_at, meta.period) == (None, "2026-W12")

    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        clear_date_metadata=True,
    )
    row = service._crud_repo.get(note_id, owner_id="u1")
    assert row is not None and (row.occurred_at, row.period) == (None, None)

    path = note_filepath(str(workspace), "", "Event")
    meta, body = read_note_file(path)
    write_note_file(path, replace(meta, occurred_at="2026-03-23"), body)
    service.reconcile_paths("ws", owner_id="u1", ws_path=str(workspace), paths=["Event.md"])
    row = service._crud_repo.get(note_id, owner_id="u1")
    assert row is not None and row.occurred_at == "2026-03-23"


def test_update_rejects_clear_combined_with_temporal_and_leaves_note_unchanged(service, workspace):
    note_id = service.save(
        "u1", "ws", str(workspace), "Combo", "Body", [], occurred_at="2026-03-22"
    )["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    with pytest.raises(TemporalMetadataError, match="cannot be combined"):
        service.update(
            note_id,
            owner_id="u1",
            ws_path=str(workspace),
            expected_sha=sha,
            clear_date_metadata=True,
            occurred_at="2026-04-01",
        )

    row = service._crud_repo.get(note_id, owner_id="u1")
    assert row is not None and row.occurred_at == "2026-03-22"


def test_edit_many_rejects_clear_combined_with_temporal_and_leaves_note_unchanged(
    service, workspace
):
    note_id = service.save(
        "u1", "ws", str(workspace), "Combo Batch", "Body", [], occurred_at="2026-03-22"
    )["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            {
                "note_id": note_id,
                "mode": "overwrite",
                "expected_sha": sha,
                "clear_date_metadata": True,
                "period": "2026-W12",
            }
        ],
    )

    assert result["applied"] is False
    assert "cannot be combined" in result["errors"][0]["error"]
    row = service._crud_repo.get(note_id, owner_id="u1")
    assert row is not None and row.occurred_at == "2026-03-22"


def test_update_rejects_malformed_period(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Bad Period", "Body", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    with pytest.raises(TemporalMetadataError, match="canonical period key"):
        service.update(
            note_id,
            owner_id="u1",
            ws_path=str(workspace),
            expected_sha=sha,
            period="not-a-period",
        )

    row = service._crud_repo.get(note_id, owner_id="u1")
    assert row is not None and row.period is None


def test_edit_many_rejects_malformed_period(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Bad Period Batch", "Body", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            {
                "note_id": note_id,
                "mode": "overwrite",
                "expected_sha": sha,
                "period": "not-a-period",
            }
        ],
    )

    assert result["applied"] is False
    assert "canonical period key" in result["errors"][0]["error"]
    row = service._crud_repo.get(note_id, owner_id="u1")
    assert row is not None and row.period is None
