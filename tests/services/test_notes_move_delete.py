"""move/list_folders/delete/list-scope/search coverage for NoteService."""

import time
from unittest.mock import patch

import pytest

from kajet_turbo import perf
from kajet_turbo.repositories.git import GitError, GitRepository
from tests.services.helpers import head_sha


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


def test_delete_rolls_back_database_teardown_and_leaves_file_untouched(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Keep me", "content", [])["note_id"]
    sha_before = head_sha(workspace, "Keep me.md")

    def fail(session, note_id_arg):
        raise RuntimeError("injected teardown failure")

    with (
        patch.object(service._link_repo, "delete_links_to_in_session", side_effect=fail),
        pytest.raises(RuntimeError, match="injected teardown failure"),
    ):
        service.delete(note_id, owner_id="u1", ws_path=str(workspace))

    assert service._crud_repo.get(note_id, owner_id="u1") is not None
    assert (workspace / "Keep me.md").exists()
    assert head_sha(workspace, "Keep me.md") == sha_before
    assert service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace)) is not None


def test_delete_perf_span_excludes_git_commit_time_from_db_ms(service, workspace, monkeypatch):
    note_id = service.save("u1", "ws", str(workspace), "Perf", "content", [])["note_id"]
    original = GitRepository.delete_file

    def slow_delete_file(self, relative_path, message):
        time.sleep(0.1)
        return original(self, relative_path, message)

    monkeypatch.setattr(GitRepository, "delete_file", slow_delete_file)

    with perf.perf_span() as span:
        service.delete(note_id, owner_id="u1", ws_path=str(workspace))

    assert span is not None
    assert span.fields["db_ms"] < 50
    assert span.fields["workspace_write_ms"] >= 90
    assert span.fields["db_ms"] + span.fields["git_ms"] <= span.fields["workspace_write_ms"]


def test_delete_git_failure_rolls_back_database_teardown(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Keep me", "content", [])["note_id"]

    # delete_file is mocked out entirely, so it never touches the filesystem — the only
    # thing this test can prove is that the row teardown rolled back with it.
    with (
        patch(
            "kajet_turbo.repositories.git.GitRepository.delete_file", side_effect=GitError("fail")
        ),
        pytest.raises(GitError),
    ):
        service.delete(note_id, owner_id="u1", ws_path=str(workspace))

    assert service._crud_repo.get(note_id, owner_id="u1") is not None


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
