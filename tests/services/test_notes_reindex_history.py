"""reindex/history/versions/restore coverage for NoteService."""

import pytest


def test_reindex_rebuilds_fts(service, workspace):
    from kajet_turbo.workspace import note_filepath, write_note_file

    path = note_filepath(str(workspace), "", "Zewnętrzna notatka")
    write_note_file(
        path,
        "ext001",
        "Zewnętrzna notatka",
        [],
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:00+00:00",
        "treść zewnętrzna",
    )
    result = service.reindex("ws", owner_id="u1", ws_path=str(workspace))
    assert result["count"] == 1
    found = service._chunk_repo.search_fts("Zewnętrzna", "ws", owner_id="u1")
    assert any(n["note_id"] == "ext001" for n in found)


def test_reindex_finds_notes_in_subfolders(service, workspace, note_file_factory):
    note_file_factory(workspace, "Root note", note_id="root-id")
    note_file_factory(workspace, "Nested note", note_id="nested-id", folder="docs")

    result = service.reindex("ws", owner_id="u1", ws_path=str(workspace))

    assert result["count"] == 2


def test_get_history_returns_commits(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Historia", "v1", [])
    note_id = result["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        content="v2",
    )

    history = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))

    assert len(history) == 2
    assert all("sha" in h and "message" in h and "timestamp" in h for h in history)


def test_get_history_raises_for_unknown_note(service, workspace):
    with pytest.raises(ValueError):
        service.get_history("nie-ma", owner_id="u1", ws_path=str(workspace))


def test_get_version_returns_historical_content(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Historia", "treść oryginalna", [])
    note_id = result["note_id"]
    sha_v1 = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha_v1, content="treść nowa"
    )

    version = service.get_version(note_id, sha_v1, owner_id="u1", ws_path=str(workspace))

    assert version["content"] == "treść oryginalna"
    assert version["note_id"] == note_id


def test_restore_version_reverts_content(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Historia", "treść oryginalna", [])
    note_id = result["note_id"]
    sha_v1 = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha_v1, content="treść nowa"
    )

    service.restore_version(note_id, sha_v1, owner_id="u1", ws_path=str(workspace))

    current = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert current.content == "treść oryginalna"


def test_restore_version_still_works_after_expected_sha_added(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Historia", "oryginalna", [])["note_id"]
    sha_v1 = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha_v1, content="nowa"
    )

    service.restore_version(note_id, sha_v1, owner_id="u1", ws_path=str(workspace))

    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.content == "oryginalna"
