from unittest.mock import patch

import pytest

from kajet_turbo.db import Database
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.notes import NoteService


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    GitRepository.init(str(ws))
    return ws


@pytest.fixture
def service(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    repo = NoteRepository(db.engine)
    yield NoteService(repo)
    db.close()


def test_save_creates_file_and_db_record(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Testowa notatka", "treść", ["python"])
    assert "note_id" in result
    note_id = result["note_id"]
    assert (workspace / "Testowa notatka.md").exists()
    note = service._repo.get(note_id, owner_id="u1")
    assert note is not None
    assert note.title == "Testowa notatka"
    assert note.owner_id == "u1"


def test_save_git_error_rolls_back_file(service, workspace):
    from kajet_turbo.repositories.git import GitError

    with (
        patch(
            "kajet_turbo.repositories.git.GitRepository.commit_file", side_effect=GitError("fail")
        ),
        pytest.raises(GitError),
    ):
        service.save("u1", "ws", str(workspace), "Git fail note", "treść", [])
    md_files = [p for p in workspace.rglob("*.md") if ".git" not in str(p)]
    assert md_files == []


def test_get_with_content_returns_none_for_wrong_owner(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Notatka", "treść", [])
    note_id = result["note_id"]
    assert service.get_with_content(note_id, owner_id="u2", ws_path=str(workspace)) is None


def test_get_with_content_returns_content(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Notatka", "moja treść", [])
    note_id = result["note_id"]
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note is not None
    assert note["content"] == "moja treść"
    assert note["title"] == "Notatka"


def test_update_git_error_reverts_file(service, workspace):
    from kajet_turbo.repositories.git import GitError

    result = service.save("u1", "ws", str(workspace), "Oryginał", "stara treść", [])
    note_id = result["note_id"]
    with (
        patch(
            "kajet_turbo.repositories.git.GitRepository.commit_file", side_effect=GitError("fail")
        ),
        pytest.raises(GitError),
    ):
        service.update(note_id, owner_id="u1", ws_path=str(workspace), content="nowa treść")
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note["content"] == "stara treść"


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
    note_id = service.save(
        "u1", "ws", str(workspace), "Move me", "content", [], folder="docs"
    )["note_id"]

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


def test_update_folder_only_keeps_path_creation_semantics(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Move me", "content", [])["note_id"]
    before = service.get(note_id, owner_id="u1")

    service.update(note_id, owner_id="u1", ws_path=str(workspace), folder="archive")

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


def test_list_scoped_by_owner(service, workspace):
    service.save("u1", "ws", str(workspace), "Notatka u1", "treść", [])
    service.save("u2", "ws", str(workspace), "Notatka u2", "treść", [])
    result_u1 = service.list("ws", owner_id="u1")
    result_u2 = service.list("ws", owner_id="u2")
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
    found = service._repo.search_fts("Zewnętrzna", "ws", owner_id="u1")
    assert any(n["note_id"] == "ext001" for n in found)


def test_get_history_returns_commits(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Historia", "v1", [])
    note_id = result["note_id"]
    service.update(note_id, owner_id="u1", ws_path=str(workspace), content="v2")

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
    service.update(note_id, owner_id="u1", ws_path=str(workspace), content="treść nowa")

    version = service.get_version(note_id, sha_v1, owner_id="u1", ws_path=str(workspace))

    assert version["content"] == "treść oryginalna"
    assert version["note_id"] == note_id


def test_restore_version_reverts_content(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Historia", "treść oryginalna", [])
    note_id = result["note_id"]
    sha_v1 = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(note_id, owner_id="u1", ws_path=str(workspace), content="treść nowa")

    service.restore_version(note_id, sha_v1, owner_id="u1", ws_path=str(workspace))

    current = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert current["content"] == "treść oryginalna"
