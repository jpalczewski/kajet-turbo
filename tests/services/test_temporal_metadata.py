from dataclasses import replace

from kajet_turbo.workspace import note_filepath, read_note_file, write_note_file


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
