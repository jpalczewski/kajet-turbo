"""move/list_folders/delete/list-scope/search coverage for NoteFolderService/NoteDeleteService."""

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import Session, select

from kajet_turbo import perf
from kajet_turbo.models import NoteShareLinkVisit
from kajet_turbo.repositories.git import GitError, GitRepository
from tests.conftest import seed_user
from tests.services.conftest import note_target, workspace_target
from tests.services.helpers import head_sha, make_flaky_db_write


def test_move_note_to_existing_folder_preserves_updated_at(
    service, folder_service, read_service, workspace
):
    (workspace / "archive").mkdir()
    note_id = service.create.save(
        workspace_target("u1", "ws", workspace), "Move me", "content", []
    )["note_id"]
    before = read_service.get(note_id, owner_id="u1")

    moved = folder_service.move(note_target("u1", "ws", workspace, note_id), folder="archive")

    after = read_service.get(note_id, owner_id="u1")
    assert moved == {"note_id": note_id, "folder": "archive"}
    assert after["folder"] == "archive"
    assert after["updated_at"] == before["updated_at"]
    assert not (workspace / "Move me.md").exists()
    assert (workspace / "archive" / "Move me.md").exists()


def test_move_note_to_root(service, folder_service, workspace):
    note_id = service.create.save(
        workspace_target("u1", "ws", workspace), "Move me", "content", [], folder="docs"
    )["note_id"]

    folder_service.move(note_target("u1", "ws", workspace, note_id), folder="")

    assert (workspace / "Move me.md").exists()
    assert not (workspace / "docs" / "Move me.md").exists()


def test_move_note_creates_missing_folder_path(service, folder_service, workspace):
    note_id = service.create.save(
        workspace_target("u1", "ws", workspace), "Move me", "content", []
    )["note_id"]

    folder_service.move(note_target("u1", "ws", workspace, note_id), folder="new/nested")

    assert (workspace / "new" / "nested" / "Move me.md").exists()


def test_move_note_os_error_on_rename_surfaces_as_git_error(
    service, folder_service, read_service, workspace, monkeypatch
):
    """The filesystem rename inside move()'s StagedChange used to be GitRepository's
    dedicated rename_file(), which normalized any OS-level failure (permissions,
    cross-device link) to GitError. That normalization must survive the move to a
    generic apply() closure — callers still only need to catch GitError."""
    note_id = service.create.save(
        workspace_target("u1", "ws", workspace), "Move me", "content", []
    )["note_id"]
    real_rename = Path.rename

    def flaky_rename(self, target):
        if self.name == "Move me.md":
            raise OSError("permission denied")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)

    with pytest.raises(GitError, match="permission denied"):
        folder_service.move(note_target("u1", "ws", workspace, note_id), folder="archive")

    assert (workspace / "Move me.md").exists()
    after = read_service.get(note_id, owner_id="u1")
    assert after["folder"] == ""


def test_move_note_db_failure_leaves_file_and_row_untouched(
    service, folder_service, read_service, workspace
):
    """#155: move() now writes its row before the git commit, inside one transaction
    that commits last — a DB-side failure must abort before either changes."""
    note_id = service.create.save(
        workspace_target("u1", "ws", workspace), "Move me", "content", []
    )["note_id"]
    sha_before = head_sha(workspace, "Move me.md")
    flaky_update = make_flaky_db_write(service.crud_repo.update_in_session)

    with (
        patch.object(service.crud_repo, "update_in_session", flaky_update),
        pytest.raises(RuntimeError, match="db exploded"),
    ):
        folder_service.move(note_target("u1", "ws", workspace, note_id), folder="archive")

    assert (workspace / "Move me.md").exists()
    assert not (workspace / "archive" / "Move me.md").exists()
    assert head_sha(workspace, "Move me.md") == sha_before
    after = read_service.get(note_id, owner_id="u1")
    assert after["folder"] == ""


def test_move_note_rejects_destination_collision(service, folder_service, workspace):
    (workspace / "archive").mkdir()
    note_id = service.create.save(workspace_target("u1", "ws", workspace), "Same", "source", [])[
        "note_id"
    ]
    service.create.save(
        workspace_target("u1", "ws", workspace), "Same", "destination", [], folder="archive"
    )

    with pytest.raises(FileExistsError):
        folder_service.move(note_target("u1", "ws", workspace, note_id), folder="archive")


def test_move_note_rejects_unindexed_destination_file(service, folder_service, workspace):
    (workspace / "archive").mkdir()
    (workspace / "archive" / "Same.md").write_text("external")
    note_id = service.create.save(workspace_target("u1", "ws", workspace), "Same", "source", [])[
        "note_id"
    ]

    with pytest.raises(FileExistsError):
        folder_service.move(note_target("u1", "ws", workspace, note_id), folder="archive")

    assert (workspace / "archive" / "Same.md").read_text() == "external"


def test_move_note_rejects_normalization_collision(service, folder_service, workspace):
    """ "A:B" moved into "archive" would land on "A B.md", already used by "A B"."""
    (workspace / "archive").mkdir()
    note_id = service.create.save(workspace_target("u1", "ws", workspace), "A:B", "source", [])[
        "note_id"
    ]
    service.create.save(
        workspace_target("u1", "ws", workspace), "A B", "destination", [], folder="archive"
    )

    with pytest.raises(FileExistsError, match="A B"):
        folder_service.move(note_target("u1", "ws", workspace, note_id), folder="archive")

    from kajet_turbo.workspace import read_note_file

    _, source_content = read_note_file(str(workspace / "A B.md"))
    _, dest_content = read_note_file(str(workspace / "archive" / "A B.md"))
    assert source_content.strip() == "source"
    assert dest_content.strip() == "destination"


def test_move_note_case_only_folder_rename_succeeds(
    service, folder_service, read_service, workspace
):
    """#181: moving a note into a folder differing only by case from its current one
    used to raise a false FileExistsError against its own not-yet-moved source on a
    case-insensitive-but-case-preserving filesystem. The fix routes the move through
    a temp name, producing the same correct end state on any filesystem — see
    NoteFolderService.move_folder's own test of the equivalent folder-level case,
    test_move_folder_case_only_rename."""
    note_id = service.create.save(
        workspace_target("u1", "ws", workspace), "N", "content", [], folder="Projekty"
    )["note_id"]

    folder_service.move(note_target("u1", "ws", workspace, note_id), folder="projekty")

    assert (workspace / "projekty" / "N.md").exists()
    after = read_service.get(note_id, owner_id="u1")
    assert after["folder"] == "projekty"


def test_update_folder_only_keeps_path_creation_semantics(service, read_service, workspace):
    note_id = service.create.save(
        workspace_target("u1", "ws", workspace), "Move me", "content", []
    )["note_id"]
    before = read_service.get(note_id, owner_id="u1")
    sha = service.version_service.get_history(note_target("u1", "ws", workspace, note_id))[0]["sha"]

    service.edit.update(
        note_target("u1", "ws", workspace, note_id), expected_sha=sha, folder="archive"
    )

    after = read_service.get(note_id, owner_id="u1")
    assert after["folder"] == "archive"
    assert after["updated_at"] != before["updated_at"]
    assert (workspace / "archive" / "Move me.md").exists()


def test_list_folders_reads_visible_directories_from_disk(folder_service, workspace):
    (workspace / "docs" / "empty").mkdir(parents=True)
    (workspace / ".hidden").mkdir()

    assert folder_service.list_folders(str(workspace)) == ["", "docs", "docs/empty"]


def test_delete_raises_for_wrong_owner(service, workspace):
    result = service.create.save(workspace_target("u1", "ws", workspace), "Notatka", "treść", [])
    note_id = result["note_id"]
    with pytest.raises(ValueError):
        service.delete.delete(note_target("u2", "ws", workspace, note_id))


def test_delete_removes_file_from_note_folder(service, workspace):
    note_id = service.create.save(
        workspace_target("u1", "ws", workspace), "Delete me", "content", [], folder="trash"
    )["note_id"]

    service.delete.delete(note_target("u1", "ws", workspace, note_id))

    assert not (workspace / "trash" / "Delete me.md").exists()


def test_delete_note_with_share_link_succeeds(service, workspace, database):
    """A NoteShareLink FKs to notes.id with no cascade (models.py) — NoteTeardown must
    delete it before the note row or this raises IntegrityError under
    PRAGMA foreign_keys=ON (db.py), leaving the note permanently undeletable."""
    seed_user(database, "u1")
    note_id = service.create.save(workspace_target("u1", "ws", workspace), "Shared", "content", [])[
        "note_id"
    ]
    link = service.share_link_repo.create(note_id, "ws", "u1")
    service.share_link_repo.record_visit(link.token, "203.0.113.1", "Browser/1")

    service.delete.delete(note_target("u1", "ws", workspace, note_id))

    assert service.crud_repo.get(note_id, owner_id="u1") is None
    assert service.share_link_repo.list_for_note(note_id) == []
    with Session(database.engine) as session:
        assert session.exec(select(NoteShareLinkVisit)).all() == []


def test_delete_note_with_revoked_share_link_succeeds(service, workspace, database):
    """revoke() only soft-deletes (sets revoked_at) — the row, and its FK to notes.id,
    survives revocation, so a revoked-but-undeleted link must not block note deletion
    either."""
    seed_user(database, "u1")
    note_id = service.create.save(workspace_target("u1", "ws", workspace), "Shared", "content", [])[
        "note_id"
    ]
    link = service.share_link_repo.create(note_id, "ws", "u1")
    service.share_link_repo.revoke("u1", note_id, link.token)

    service.delete.delete(note_target("u1", "ws", workspace, note_id))

    assert service.crud_repo.get(note_id, owner_id="u1") is None


def test_delete_rolls_back_database_teardown_and_leaves_file_untouched(
    service, read_service, workspace
):
    note_id = service.create.save(
        workspace_target("u1", "ws", workspace), "Keep me", "content", []
    )["note_id"]
    sha_before = head_sha(workspace, "Keep me.md")

    def fail(session, note_id_arg):
        raise RuntimeError("injected teardown failure")

    with (
        patch.object(service.link_repo, "delete_links_to_in_session", side_effect=fail),
        pytest.raises(RuntimeError, match="injected teardown failure"),
    ):
        service.delete.delete(note_target("u1", "ws", workspace, note_id))

    assert service.crud_repo.get(note_id, owner_id="u1") is not None
    assert (workspace / "Keep me.md").exists()
    assert head_sha(workspace, "Keep me.md") == sha_before
    assert read_service.get_with_content(note_target("u1", "ws", workspace, note_id)) is not None


def test_delete_perf_span_excludes_git_commit_time_from_db_ms(service, workspace, monkeypatch):
    note_id = service.create.save(workspace_target("u1", "ws", workspace), "Perf", "content", [])[
        "note_id"
    ]
    original = GitRepository.delete_file

    def slow_delete_file(self, relative_path, message):
        time.sleep(0.1)
        return original(self, relative_path, message)

    monkeypatch.setattr(GitRepository, "delete_file", slow_delete_file)

    with perf.perf_span() as span:
        service.delete.delete(note_target("u1", "ws", workspace, note_id))

    assert span is not None
    assert span.fields["db_ms"] < 50
    assert span.fields["workspace_write_ms"] >= 90
    assert span.fields["db_ms"] + span.fields["git_ms"] <= span.fields["workspace_write_ms"]


def test_delete_git_failure_rolls_back_database_teardown(service, workspace):
    note_id = service.create.save(
        workspace_target("u1", "ws", workspace), "Keep me", "content", []
    )["note_id"]

    # delete_file is mocked out entirely, so it never touches the filesystem — the only
    # thing this test can prove is that the row teardown rolled back with it.
    with (
        patch(
            "kajet_turbo.repositories.git.GitRepository.delete_file", side_effect=GitError("fail")
        ),
        pytest.raises(GitError),
    ):
        service.delete.delete(note_target("u1", "ws", workspace, note_id))

    assert service.crud_repo.get(note_id, owner_id="u1") is not None


def test_list_scoped_by_owner(service, read_service, workspace):
    service.create.save(workspace_target("u1", "ws", workspace), "Notatka u1", "treść", [])
    service.create.save(workspace_target("u2", "ws", workspace), "Notatka u2", "treść", [])
    result_u1 = read_service.list_notes(workspace_target("u1", "ws", workspace))
    result_u2 = read_service.list_notes(workspace_target("u2", "ws", workspace))
    assert len(result_u1) == 1 and result_u1[0]["title"] == "Notatka u1"
    assert len(result_u2) == 1 and result_u2[0]["title"] == "Notatka u2"


def test_search_across_workspaces(service, search_service, workspace):
    ws2 = workspace.parent / "ws2"
    ws2.mkdir(parents=True)
    GitRepository.init(str(ws2))
    service.create.save(workspace_target("u1", "ws", workspace), "Python w ws1", "asyncio", [])
    service.create.save(workspace_target("u1", "ws2", ws2), "Python w ws2", "asyncio", [])
    results = search_service.search("Python", ["ws", "ws2"], owner_id="u1", limit=10)
    titles = [r["title"] for r in results]
    assert "Python w ws1" in titles
    assert "Python w ws2" in titles
