"""move/list_folders/delete/list-scope/search coverage for NoteService."""

import pytest

from kajet_turbo.repositories.git import GitRepository


def test_move_note_to_existing_folder_preserves_updated_at(service, workspace):
    (workspace / "archive").mkdir()
    note_id = service.save("u1", "ws", str(workspace), "Move me", "content", [])["note_id"]
    before = service.get(note_id, owner_id="u1")

    moved = service.move(note_id, owner_id="u1", ws_path=str(workspace), folder="archive")

    after = service.get(note_id, owner_id="u1")
    assert moved == {"note_id": note_id, "folder": "archive"}
    assert after["folder"] == "archive"
    assert after["updated_at"] == before["updated_at"]
    assert not (workspace / "Move me.md").exists()
    assert (workspace / "archive" / "Move me.md").exists()


def test_move_note_to_root(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Move me", "content", [], folder="docs")[
        "note_id"
    ]

    service.move(note_id, owner_id="u1", ws_path=str(workspace), folder="")

    assert (workspace / "Move me.md").exists()
    assert not (workspace / "docs" / "Move me.md").exists()


def test_move_note_creates_missing_folder_path(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Move me", "content", [])["note_id"]

    service.move(note_id, owner_id="u1", ws_path=str(workspace), folder="new/nested")

    assert (workspace / "new" / "nested" / "Move me.md").exists()


def test_move_note_rejects_destination_collision(service, workspace):
    (workspace / "archive").mkdir()
    note_id = service.save("u1", "ws", str(workspace), "Same", "source", [])["note_id"]
    service.save("u1", "ws", str(workspace), "Same", "destination", [], folder="archive")

    with pytest.raises(FileExistsError):
        service.move(note_id, owner_id="u1", ws_path=str(workspace), folder="archive")


def test_move_note_rejects_unindexed_destination_file(service, workspace):
    (workspace / "archive").mkdir()
    (workspace / "archive" / "Same.md").write_text("external")
    note_id = service.save("u1", "ws", str(workspace), "Same", "source", [])["note_id"]

    with pytest.raises(FileExistsError):
        service.move(note_id, owner_id="u1", ws_path=str(workspace), folder="archive")

    assert (workspace / "archive" / "Same.md").read_text() == "external"


def test_move_note_rejects_normalization_collision(service, workspace):
    """ "A:B" moved into "archive" would land on "A B.md", already used by "A B"."""
    (workspace / "archive").mkdir()
    note_id = service.save("u1", "ws", str(workspace), "A:B", "source", [])["note_id"]
    service.save("u1", "ws", str(workspace), "A B", "destination", [], folder="archive")

    with pytest.raises(FileExistsError, match="A B"):
        service.move(note_id, owner_id="u1", ws_path=str(workspace), folder="archive")

    from kajet_turbo.workspace import read_note_file

    _, source_content = read_note_file(str(workspace / "A B.md"))
    _, dest_content = read_note_file(str(workspace / "archive" / "A B.md"))
    assert source_content.strip() == "source"
    assert dest_content.strip() == "destination"


def test_update_folder_only_keeps_path_creation_semantics(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Move me", "content", [])["note_id"]
    before = service.get(note_id, owner_id="u1")
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha, folder="archive"
    )

    after = service.get(note_id, owner_id="u1")
    assert after["folder"] == "archive"
    assert after["updated_at"] != before["updated_at"]
    assert (workspace / "archive" / "Move me.md").exists()


def test_list_folders_reads_visible_directories_from_disk(service, workspace):
    (workspace / "docs" / "empty").mkdir(parents=True)
    (workspace / ".hidden").mkdir()

    assert service.list_folders(str(workspace)) == ["", "docs", "docs/empty"]


def test_delete_raises_for_wrong_owner(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Notatka", "treść", [])
    note_id = result["note_id"]
    with pytest.raises(ValueError):
        service.delete(note_id, owner_id="u2", ws_path=str(workspace))


def test_delete_removes_file_from_note_folder(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Delete me", "content", [], folder="trash")[
        "note_id"
    ]

    service.delete(note_id, owner_id="u1", ws_path=str(workspace))

    assert not (workspace / "trash" / "Delete me.md").exists()


def test_list_scoped_by_owner(service, workspace):
    service.save("u1", "ws", str(workspace), "Notatka u1", "treść", [])
    service.save("u2", "ws", str(workspace), "Notatka u2", "treść", [])
    result_u1 = service.list_notes("ws", owner_id="u1")
    result_u2 = service.list_notes("ws", owner_id="u2")
    assert len(result_u1) == 1 and result_u1[0]["title"] == "Notatka u1"
    assert len(result_u2) == 1 and result_u2[0]["title"] == "Notatka u2"


def test_search_across_workspaces(service, workspace):
    ws2 = workspace.parent / "ws2"
    ws2.mkdir(parents=True)
    GitRepository.init(str(ws2))
    service.save("u1", "ws", str(workspace), "Python w ws1", "asyncio", [])
    service.save("u1", "ws2", str(ws2), "Python w ws2", "asyncio", [])
    results = service.search("Python", ["ws", "ws2"], owner_id="u1", limit=10)
    titles = [r["title"] for r in results]
    assert "Python w ws1" in titles
    assert "Python w ws2" in titles
