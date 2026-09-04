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
