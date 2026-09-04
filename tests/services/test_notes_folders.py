"""Folder move / merge / prune coverage for NoteService."""

from unittest.mock import patch

import pytest

from tests.services.conftest import seed_user
from tests.services.helpers import head_sha, make_flaky_db_write


@pytest.fixture(autouse=True)
def _seed_default_owner(database):
    # Folder moves rewrite backlinks, which now enqueue reindex_note jobs (user_id FK).
    seed_user(database, "u1")


def _mv(service, workspace, src, dst):
    return service.move_folder(src, dst, owner_id="u1", ws_path=str(workspace), workspace="ws")


def test_move_folder_renames_with_notes(service, workspace):
    a = service.save("u1", "ws", str(workspace), "A", "x", [], folder="people")["note_id"]
    service.save("u1", "ws", str(workspace), "B", "y", [], folder="people")

    result = _mv(service, workspace, "people", "team")

    assert result == {"moved": 2, "src": "people", "dst": "team"}
    assert (workspace / "team" / "A.md").exists()
    assert (workspace / "team" / "B.md").exists()
    assert not (workspace / "people").exists()
    assert service.get(a, owner_id="u1")["folder"] == "team"


def test_move_folder_merges_into_existing(service, workspace):
    service.save("u1", "ws", str(workspace), "X", "x", [], folder="a")
    service.save("u1", "ws", str(workspace), "Y", "y", [], folder="b")

    result = _mv(service, workspace, "a", "b")

    assert result["moved"] == 1
    assert (workspace / "b" / "X.md").exists()
    assert (workspace / "b" / "Y.md").exists()
    assert not (workspace / "a").exists()


def test_move_folder_collision_aborts_atomically(service, workspace):
    service.save("u1", "ws", str(workspace), "Same", "source", [], folder="a")
    service.save("u1", "ws", str(workspace), "Same", "destination", [], folder="b")

    result = _mv(service, workspace, "a", "b")

    assert result["conflicts"] == [{"title": "Same", "folder": "b"}]
    # nothing moved
    assert (workspace / "a" / "Same.md").exists()
    assert "destination" in (workspace / "b" / "Same.md").read_text()


def test_move_folder_rejects_normalization_collision_atomically(service, workspace):
    """ "A:B" in "a" would land on "A B.md" in "b", already used by "A B". A third,
    non-colliding note in "a" must also NOT move — proves the pre-flight conflict loop
    catches this before any rename() happens, not mid-walk."""
    service.save("u1", "ws", str(workspace), "A:B", "source", [], folder="a")
    service.save("u1", "ws", str(workspace), "A B", "destination", [], folder="b")
    service.save("u1", "ws", str(workspace), "Innocent", "bystander", [], folder="a")

    result = _mv(service, workspace, "a", "b")

    from kajet_turbo.workspace import read_note_file

    assert "conflicts" in result
    assert (workspace / "a" / "A B.md").exists()
    assert (workspace / "a" / "Innocent.md").exists()
    assert not (workspace / "b" / "Innocent.md").exists()
    _, dest_content = read_note_file(str(workspace / "b" / "A B.md"))
    assert dest_content.strip() == "destination"


def test_move_folder_rejects_sibling_collision_within_same_move(service, workspace):
    """Two notes in the same source folder whose titles normalize to the same filename
    can't be created via save() any more (it rejects that collision too), but a pair
    from before this fix shipped can still exist in the DB. Moving them together must
    still conflict via the in-batch `claimed` tracking, not just against a pre-existing
    destination note — proves note_path_conflict()'s static pre-move snapshot alone
    isn't enough here, since both siblings share one (stale) folder value in it."""
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()
    service._crud_repo.insert("n1", "ws", "u1", "A:B", [], now, now, "a", None, None)
    service._crud_repo.insert("n2", "ws", "u1", "A B", [], now, now, "a", None, None)

    result = _mv(service, workspace, "a", "b")

    assert "conflicts" in result
    assert not (workspace / "b").exists()


def test_move_folder_rejects_case_only_sibling_collision_within_same_move(service, workspace):
    """Two notes moved together whose titles differ only by case must still conflict via
    the in-batch `claimed` tracking — the same collision `test_move_folder_rejects_
    sibling_collision_within_same_move` proves for normalization, but for case. Case-twin
    rows can no longer be created via save() after this fix, so — like that test — this
    simulates a pair left over from before the fix shipped, inserted directly."""
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()
    service._crud_repo.insert("n1", "ws", "u1", "Readme", [], now, now, "a", None, None)
    service._crud_repo.insert("n2", "ws", "u1", "readme", [], now, now, "a", None, None)

    result = _mv(service, workspace, "a", "b")

    assert "conflicts" in result
    assert not (workspace / "b").exists()


def test_move_folder_rejects_collision_with_orphan_file_on_disk(service, workspace):
    """A file with no matching DB row sitting at the destination must still block the
    move — the pre-flight loop only checks DB rows (a disk check there would falsely
    trip on a case-only rename's own not-yet-relocated source), so this is caught only
    once the source is safely out of the way, right before the note would land there."""
    note_id = service.save("u1", "ws", str(workspace), "N", "x", [], folder="a")["note_id"]
    (workspace / "b").mkdir()
    (workspace / "b" / "N.md").write_text("orphan content\n")

    with pytest.raises(FileExistsError):
        _mv(service, workspace, "a", "b")

    assert (workspace / "b" / "N.md").read_text() == "orphan content\n"
    assert (workspace / "a" / "N.md").exists()
    assert service.get(note_id, owner_id="u1")["folder"] == "a"


def test_move_folder_case_only_rename(service, workspace):
    nid = service.save("u1", "ws", str(workspace), "N", "x", [], folder="Osoby")["note_id"]

    result = _mv(service, workspace, "Osoby", "osoby")

    assert result["moved"] == 1
    assert (workspace / "osoby" / "N.md").exists()
    assert service.get(nid, owner_id="u1")["folder"] == "osoby"
    folders = service.list_folders(str(workspace))
    assert "osoby" in folders and "Osoby" not in folders


def test_move_folder_moves_nested_subfolders(service, workspace):
    nid = service.save("u1", "ws", str(workspace), "Deep", "z", [], folder="a/sub")["note_id"]

    _mv(service, workspace, "a", "b")

    assert (workspace / "b" / "sub" / "Deep.md").exists()
    assert not (workspace / "a").exists()
    assert service.get(nid, owner_id="u1")["folder"] == "b/sub"


def test_move_folder_rejects_into_own_subtree(service, workspace):
    service.save("u1", "ws", str(workspace), "N", "x", [], folder="a")

    with pytest.raises(ValueError):
        _mv(service, workspace, "a", "a/b")


def test_move_folder_rewrites_external_backlink(service, workspace):
    service.save("u1", "ws", str(workspace), "Target", "content", [], folder="src")
    service.save("u1", "ws", str(workspace), "Linker", "see [[src/Target]]", [])

    _mv(service, workspace, "src", "dst")

    body = (workspace / "Linker.md").read_text()
    assert "[[dst/Target]]" in body
    assert "[[src/Target]]" not in body


def test_move_folder_rewrites_intra_folder_link(service, workspace):
    # X links to Y, both in the moved folder — the link must follow the move.
    service.save("u1", "ws", str(workspace), "Y", "target", [], folder="a")
    service.save("u1", "ws", str(workspace), "X", "see [[a/Y]]", [], folder="a")

    _mv(service, workspace, "a", "b")

    body = (workspace / "b" / "X.md").read_text()
    assert "[[b/Y]]" in body
    assert "[[a/Y]]" not in body


def test_move_note_prunes_empty_parents(service, workspace):
    nid = service.save("u1", "ws", str(workspace), "N", "x", [], folder="deep/nested")["note_id"]

    service.move(nid, owner_id="u1", ws_path=str(workspace), folder="")

    assert (workspace / "N.md").exists()
    assert not (workspace / "deep").exists()


def test_move_note_keeps_gitkeep_folder(service, workspace):
    (workspace / "keep").mkdir()
    (workspace / "keep" / ".gitkeep").touch()
    nid = service.save("u1", "ws", str(workspace), "N", "x", [], folder="keep")["note_id"]

    service.move(nid, owner_id="u1", ws_path=str(workspace), folder="")

    assert (workspace / "keep").exists()


def test_prune_empty_folders_removes_orphans_keeps_gitkeep(service, workspace):
    (workspace / "orphan" / "child").mkdir(parents=True)
    (workspace / "kept").mkdir()
    (workspace / "kept" / ".gitkeep").touch()

    result = service.prune_empty_folders(str(workspace))

    assert not (workspace / "orphan").exists()
    assert (workspace / "kept").exists()
    assert "orphan" in result["pruned"]


def test_move_folder_db_failure_leaves_git_committed_and_rows_healable(service, workspace):
    """#170: move_folder now commits the git tree unconditionally *first* (matching
    pre-#155 behavior), then writes every note's folder-column update in one DB
    transaction of its own — so a DB-side failure on note k rolls back every note's row,
    but the git commit recording the already-completed file move has already landed
    regardless.
    This restores the self-healing direction: rows lag behind an already-correct tree,
    which is exactly what reconcile_paths heals, and history for the note's new path is
    real (not empty, which was the actual observable symptom of the pre-fix bug)."""
    a = service.save("u1", "ws", str(workspace), "A", "x", [], folder="people")["note_id"]
    b = service.save("u1", "ws", str(workspace), "B", "y", [], folder="people")["note_id"]
    sha_a_before = head_sha(workspace, "people/A.md")

    flaky_update = make_flaky_db_write(service._crud_repo.update_in_session, fail_on_call=2)

    with (
        patch.object(service._crud_repo, "update_in_session", flaky_update),
        pytest.raises(RuntimeError, match="db exploded"),
    ):
        _mv(service, workspace, "people", "team")

    assert not (workspace / "people").exists()
    assert (workspace / "team" / "A.md").exists()
    assert (workspace / "team" / "B.md").exists()
    # The move's git commit already landed (git-first), so history for the old path now
    # ends at that commit rather than staying at its pre-move sha.
    assert head_sha(workspace, "people/A.md") != sha_a_before
    # DB rows lag behind the already-committed tree — the healable direction.
    assert service.get(a, owner_id="u1")["folder"] == "people"
    assert service.get(b, owner_id="u1")["folder"] == "people"

    service.reconcile_paths(
        "ws", owner_id="u1", ws_path=str(workspace), paths=["team/A.md", "team/B.md"]
    )
    assert service.get(a, owner_id="u1")["folder"] == "team"
    assert service.get(b, owner_id="u1")["folder"] == "team"
    # The actual #170 symptom: before the fix, a healed row pointed history lookups at a
    # git path with zero commits. Now the move's commit is there to find.
    assert service.get_history(a, owner_id="u1", ws_path=str(workspace)) != []
    assert service.get_history(b, owner_id="u1", ws_path=str(workspace)) != []


def test_move_folder_git_failure_leaves_nothing_committed_or_written(service, workspace):
    """Git now commits before any DB work starts, so a commit_changes failure means the DB
    write phase never begins at all — no repository_operation call, no row change — unlike
    the pre-#170 shape where a DB-write-shaped failure could roll back rows that had
    already been written. The temp-dir choreography's own move+rollback is independent of
    this and still isn't covered here: the files stay at the new location on disk with no
    git commit recording it, which is the (unchanged, still-existing) gap `services/notes/
    CLAUDE.md`'s #155 section documents."""
    from kajet_turbo.repositories.git import GitError, GitRepository

    a = service.save("u1", "ws", str(workspace), "A", "x", [], folder="people")["note_id"]

    with (
        patch.object(GitRepository, "commit_changes", side_effect=GitError("fail")),
        patch.object(service._crud_repo, "update_in_session") as update_in_session,
        pytest.raises(GitError, match="fail"),
    ):
        _mv(service, workspace, "people", "team")

    update_in_session.assert_not_called()
    assert not (workspace / "people").exists()
    assert (workspace / "team" / "A.md").exists()
    assert service.get(a, owner_id="u1")["folder"] == "people"


def test_move_folder_with_no_notes_logs_nothing(service, workspace, capsys):
    """#172: an aux-file-only folder move touches zero notes, so the `if notes:` guard
    around the DB write never opens a transaction — zero repository_operation calls, not a
    suppressed count=0 one."""
    from kajet_turbo.log import setup_logging
    from kajet_turbo.repositories.git import GitRepository
    from tests.helpers import entries_named, read_log_entries

    setup_logging()
    (workspace / "empty").mkdir()
    (workspace / "empty" / ".gitkeep").touch()
    GitRepository(str(workspace)).commit_changes(
        removed=[], added=["empty/.gitkeep"], message="init"
    )

    result = _mv(service, workspace, "empty", "renamed")

    assert result == {"moved": 0, "src": "empty", "dst": "renamed"}
    assert (workspace / "renamed" / ".gitkeep").exists()
    entries = entries_named(read_log_entries(capsys), "repository_operation")
    move_ops = [e for e in entries if e.get("operation") == "notes.move_folder"]
    assert move_ops == []


def test_move_folder_refuses_above_max_notes_ceiling(service, workspace, monkeypatch):
    """#171: an oversized folder move refuses before touching disk — unlike rename_tag/
    _rewrite_backlinks, a folder move has a real workaround (move a subfolder at a time),
    so a hard ceiling is the right shape here, checked before the irreversible temp-dir
    choreography begins."""
    import kajet_turbo.services.notes.folders as folders_module

    monkeypatch.setattr(folders_module, "_MOVE_FOLDER_MAX_NOTES", 2)
    a = service.save("u1", "ws", str(workspace), "A", "x", [], folder="people")["note_id"]
    b = service.save("u1", "ws", str(workspace), "B", "y", [], folder="people")["note_id"]
    c = service.save("u1", "ws", str(workspace), "C", "z", [], folder="people")["note_id"]

    with pytest.raises(ValueError, match=r"people.*3 notes.*2"):
        _mv(service, workspace, "people", "team")

    assert (workspace / "people").exists()
    assert not (workspace / "team").exists()
    for note_id in (a, b, c):
        assert service.get(note_id, owner_id="u1")["folder"] == "people"


def test_move_folder_marks_affected_sources_dirty_even_when_backlink_rewrite_fails(
    database, workspace, monkeypatch
):
    """#171: _rewrite_backlinks now chunks internally, so a failure partway through can
    leave some (or, as pinned here, none) of the linking sources rewritten. mark_and_enqueue
    must still run — it's the lazy ReconcileLinksHandler's safety net for exactly this case
    — rather than being skipped because the exception propagated before move_folder reached
    it."""
    from kajet_turbo.repositories.git import GitError, GitRepository
    from kajet_turbo.services.notes import links as links_module
    from tests.services.helpers import build_reconcile_wiring

    service, _jobs, dirty, _dangling, _handler = build_reconcile_wiring(database, workspace)
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [], folder="src")["note_id"]
    source_ids = {
        service.save("u1", "ws", str(workspace), f"Source {i}", "[[src/Target]]", [])["note_id"]
        for i in range(3)
    }

    monkeypatch.setattr(links_module, "MAX_BATCH_COMMIT_SIZE", 1)
    # call 1: move_folder's own git commit for the move itself — must land. call 2: the
    # first (of three) rewrite_backlinks chunks — fails, so zero sources get fixed.
    flaky_commit = make_flaky_db_write(
        GitRepository.commit_changes, fail_on_call=2, message="fail", exc=GitError
    )
    with (
        patch.object(GitRepository, "commit_changes", flaky_commit),
        pytest.raises(GitError, match="fail"),
    ):
        _mv(service, workspace, "src", "dst")

    assert service.get(tid, owner_id="u1")["folder"] == "dst"  # the move itself landed
    # affected_sources also includes the moved note itself; what matters here is that the
    # *external* linking sources — the ones rewrite_backlinks failed to fix — are in it too.
    assert source_ids <= set(dirty.list_dirty("u1", "ws"))
