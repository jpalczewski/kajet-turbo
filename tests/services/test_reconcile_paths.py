"""reconcile_paths / reindex coverage for #107: insert/update/remove from disk,
the deletion safety valve, and dangling-link healing on insert and removal."""

from pathlib import Path

import pytest

from kajet_turbo.services.notes.service import (
    _RECONCILE_MAX_DELETE_RATIO,
    _RECONCILE_MIN_DELETE_FLOOR,
)
from kajet_turbo.workspace import note_filepath, write_note_file
from tests.services.helpers import build_reconcile_wiring


def _rel(ws_path, filepath: str) -> str:
    return str(Path(filepath).relative_to(ws_path))


def test_reconcile_inserts_new_file_not_yet_in_db(service, workspace, note_file_factory):
    path = note_file_factory(workspace, "New note", note_id="new1", tags=["x"])

    report = service.reconcile_paths(
        "ws", owner_id="u1", ws_path=str(workspace), paths=[_rel(workspace, path)]
    )

    assert report.inserted == ["new1"]
    assert report.updated == []
    assert report.removed == []
    note = service._crud_repo.get("new1", owner_id="u1")
    assert note is not None and note.title == "New note"
    assert service._chunk_repo.search_fts("New note", "ws", owner_id="u1")


def test_reconcile_removes_row_whose_file_is_gone(service, workspace, note_file_factory):
    path = note_file_factory(workspace, "Gone", note_id="gone1")
    relative = _rel(workspace, path)
    service.reconcile_paths("ws", owner_id="u1", ws_path=str(workspace), paths=[relative])
    assert service._crud_repo.get("gone1", owner_id="u1") is not None

    Path(path).unlink()
    report = service.reconcile_paths("ws", owner_id="u1", ws_path=str(workspace), paths=[relative])

    assert report.removed == ["gone1"]
    assert service._crud_repo.get("gone1", owner_id="u1") is None
    assert service._chunk_repo.get_chunks("gone1") == []


def test_reconcile_updates_drifted_metadata_without_touching_other_notes(
    service, workspace, note_file_factory
):
    untouched_path = note_file_factory(workspace, "Untouched", note_id="stay1", tags=["keep"])
    path = note_file_factory(workspace, "Old title", note_id="drift1", tags=["a"], folder="")
    paths = [_rel(workspace, untouched_path), _rel(workspace, path)]
    service.reconcile_paths("ws", owner_id="u1", ws_path=str(workspace), paths=paths)
    before = service._crud_repo.get("drift1", owner_id="u1")
    assert before is not None
    generation_before = before.index_generation

    # Hand-edit the frontmatter in place: same id, new title/tags/folder — simulates a
    # user editing the file directly rather than going through save()/edit_note.
    new_path = note_filepath(str(workspace), "moved", "New title")
    Path(new_path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).rename(new_path)
    from kajet_turbo.workspace import NoteFrontmatter

    write_note_file(
        new_path,
        NoteFrontmatter(
            id="drift1",
            title="New title",
            tags=["b"],
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-02-02T00:00:00+00:00",
        ),
        "treść",
    )
    new_paths = [_rel(workspace, untouched_path), _rel(workspace, new_path), _rel(workspace, path)]

    report = service.reconcile_paths("ws", owner_id="u1", ws_path=str(workspace), paths=new_paths)

    # The hand-moved file is an UPDATE (same id, new path) — not a delete+insert.
    assert report.updated == ["drift1"]
    assert report.inserted == []
    assert report.removed == []
    after = service._crud_repo.get("drift1", owner_id="u1")
    assert after is not None
    assert (after.folder, after.title) == ("moved", "New title")
    assert after.index_generation == generation_before + 1
    # The untouched note's row is exactly as it was — no wipe side effect.
    stay = service._crud_repo.get("stay1", owner_id="u1")
    assert stay is not None and stay.title == "Untouched"


def test_reconcile_safety_valve_refuses_mass_deletion_and_leaves_db_untouched(
    service, workspace, note_file_factory
):
    paths = []
    for i in range(10):
        p = note_file_factory(workspace, f"Note {i}", note_id=f"n{i}")
        paths.append(_rel(workspace, p))
    service.reconcile_paths("ws", owner_id="u1", ws_path=str(workspace), paths=paths)

    # Delete enough files to exceed both the ratio and the absolute floor.
    to_delete = paths[: _RECONCILE_MIN_DELETE_FLOOR + 1]
    assert len(to_delete) / 10 > _RECONCILE_MAX_DELETE_RATIO
    for relative in to_delete:
        (workspace / relative).unlink()

    with pytest.raises(ValueError, match="would delete"):
        service.reconcile_paths("ws", owner_id="u1", ws_path=str(workspace), paths=paths)

    # Nothing changed: every one of the 10 rows, including the "missing" ones, is intact.
    for i in range(10):
        assert service._crud_repo.get(f"n{i}", owner_id="u1") is not None


def test_reconcile_below_floor_deletes_without_refusing(service, workspace, note_file_factory):
    """A small workspace losing a couple of notes to a legitimate cleanup is never
    blocked by the ratio alone — the absolute floor guards it."""
    paths = []
    for i in range(3):
        p = note_file_factory(workspace, f"Small {i}", note_id=f"s{i}")
        paths.append(_rel(workspace, p))
    service.reconcile_paths("ws", owner_id="u1", ws_path=str(workspace), paths=paths)

    (workspace / paths[0]).unlink()
    (workspace / paths[1]).unlink()

    report = service.reconcile_paths("ws", owner_id="u1", ws_path=str(workspace), paths=paths)

    assert set(report.removed) == {"s0", "s1"}


def test_reconcile_skips_unreadable_file_without_deleting_its_row(
    service, workspace, note_file_factory
):
    path = note_file_factory(workspace, "Broken", note_id="broken1")
    relative = _rel(workspace, path)
    service.reconcile_paths("ws", owner_id="u1", ws_path=str(workspace), paths=[relative])

    Path(path).write_text("---\ntitle: [unclosed\n---\nbody\n")

    report = service.reconcile_paths("ws", owner_id="u1", ws_path=str(workspace), paths=[relative])

    assert report.unreadable_paths == [relative]
    assert report.removed == []
    assert report.inserted == []
    # The row survives — a parse hiccup must never look like a missing file.
    assert service._crud_repo.get("broken1", owner_id="u1") is not None


def test_reconcile_reports_duplicate_id_and_keeps_the_first(service, workspace):
    from kajet_turbo.workspace import NoteFrontmatter

    def write(rel_title: str) -> str:
        p = note_filepath(str(workspace), "", rel_title)
        write_note_file(
            p,
            NoteFrontmatter(
                id="dup1",
                title=rel_title,
                tags=[],
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            ),
            "treść",
        )
        return p

    first = write("A first")
    second = write("B second")
    paths = sorted([_rel(workspace, first), _rel(workspace, second)])

    report = service.reconcile_paths("ws", owner_id="u1", ws_path=str(workspace), paths=paths)

    assert report.duplicate_ids == ["dup1"]
    assert report.inserted == ["dup1"]
    note = service._crud_repo.get("dup1", owner_id="u1")
    assert note is not None
    # The kept copy is whichever file sorts first by path.
    assert note.title == "A first"


def test_reindex_removes_orphan_row_with_no_matching_disk_path(
    service, workspace, note_file_factory
):
    """A full reindex must catch a DB row whose computed path matches no file on disk
    (stale sanitization, a prior bug's residue) — not just files that currently exist.
    Simulated by inserting a row directly, bypassing the file write."""
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()
    service._crud_repo.insert("phantom1", "ws", "u1", "Phantom", [], now, now, folder="")
    note_file_factory(workspace, "Real note", note_id="real1")

    result = service.reindex("ws", owner_id="u1", ws_path=str(workspace))

    assert result["count"] == 1
    assert service._crud_repo.get("phantom1", owner_id="u1") is None
    assert service._crud_repo.get("real1", owner_id="u1") is not None


def test_reconcile_heals_dangling_link_when_target_appears(database, git_workspace_factory):
    """A source note with a wikilink to a not-yet-existing note is dangling; when
    reconcile_paths inserts the target from a hand-created file, the dangling link
    resolves — mirrors save()'s affected_sources ordering."""
    from tests.services.conftest import seed_user

    seed_user(database, "u1")
    ws = git_workspace_factory("u1/ws")
    service, _jobs, dirty, dangling, handler = build_reconcile_wiring(database, ws.parent.parent)

    service.save("u1", "ws", str(ws), "Source", "[[Target]]", [])
    assert dangling.exists("u1", "ws") is True

    from kajet_turbo.workspace import NoteFrontmatter, note_filepath, write_note_file

    target_path = note_filepath(str(ws), "", "Target")
    write_note_file(
        target_path,
        NoteFrontmatter(
            id="target1",
            title="Target",
            tags=[],
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        ),
        "treść",
    )
    service.reconcile_paths("ws", owner_id="u1", ws_path=str(ws), paths=[_rel(ws, target_path)])
    assert dirty.list_dirty("u1", "ws")

    handler({"user_id": "u1", "workspace": "ws", "mode": "targeted"})

    assert dangling.exists("u1", "ws") is False


def test_reconcile_heals_link_to_old_title_when_target_renamed(database, git_workspace_factory):
    """A note that hand-renames on disk (same id, new title) must requeue any source
    that linked to its OLD title, not just leave the stale graph edge in place — the
    ``changed_titles.add(existing.title)`` half of the affected-titles union."""
    from tests.services.conftest import seed_user

    seed_user(database, "u1")
    ws = git_workspace_factory("u1/ws")
    service, _jobs, dirty, dangling, handler = build_reconcile_wiring(database, ws.parent.parent)

    target_id = service.save("u1", "ws", str(ws), "Old title", "treść", [])["note_id"]
    service.save("u1", "ws", str(ws), "Source", "[[Old title]]", [])
    assert dangling.exists("u1", "ws") is False

    from kajet_turbo.workspace import NoteFrontmatter, note_filepath, write_note_file

    old_path = note_filepath(str(ws), "", "Old title")
    new_path = note_filepath(str(ws), "", "New title")
    Path(old_path).rename(new_path)
    write_note_file(
        new_path,
        NoteFrontmatter(
            id=target_id,
            title="New title",
            tags=[],
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        ),
        "treść",
    )

    report = service.reconcile_paths(
        "ws", owner_id="u1", ws_path=str(ws), paths=[_rel(ws, new_path)]
    )
    assert report.updated == [target_id]
    assert dirty.list_dirty("u1", "ws")

    handler({"user_id": "u1", "workspace": "ws", "mode": "targeted"})

    # Source's stored edge pointed at the note under "Old title"; that title no longer
    # resolves to anything, so re-resolution must turn it dangling, not leave it stale.
    assert dangling.exists("u1", "ws") is True


def test_reconcile_heals_dangling_link_when_target_removed(database, git_workspace_factory):
    """Deleting a note's file via reconcile must requeue any source that linked to
    it — same affected_sources ordering as delete()."""
    from tests.services.conftest import seed_user

    seed_user(database, "u1")
    ws = git_workspace_factory("u1/ws")
    service, _jobs, dirty, dangling, handler = build_reconcile_wiring(database, ws.parent.parent)

    target_id = service.save("u1", "ws", str(ws), "Target", "treść", [])["note_id"]
    service.save("u1", "ws", str(ws), "Source", "[[Target]]", [])
    assert dangling.exists("u1", "ws") is False

    from kajet_turbo.workspace import note_filepath

    target_path = note_filepath(str(ws), "", "Target")
    relative = _rel(ws, target_path)
    Path(target_path).unlink()

    report = service.reconcile_paths("ws", owner_id="u1", ws_path=str(ws), paths=[relative])
    assert report.removed == [target_id]
    assert dirty.list_dirty("u1", "ws")

    handler({"user_id": "u1", "workspace": "ws", "mode": "targeted"})

    assert dangling.exists("u1", "ws") is True


def test_reconcile_holds_workspace_lock_against_concurrent_save(
    service, workspace, note_file_factory, monkeypatch
):
    """reconcile_paths is @workspace_write_transaction — a concurrent save() on the
    same workspace must block until reconcile releases the lock, not race it."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    path = note_file_factory(workspace, "Existing", note_id="exist1")
    relative = _rel(workspace, path)

    reconcile_started = Event()
    release_reconcile = Event()
    save_acquired = Event()
    real_insert = service._crud_repo.insert_in_session
    real_check_unique = service._crud_repo.check_unique

    def paused_insert(session, note):
        reconcile_started.set()
        assert release_reconcile.wait(timeout=5)
        return real_insert(session, note)

    def signalling_check_unique(*args, **kwargs):
        # First thing save() calls once it holds the lock — proves the lock was
        # actually acquired, not just that the thread started running.
        save_acquired.set()
        return real_check_unique(*args, **kwargs)

    monkeypatch.setattr(service._crud_repo, "insert_in_session", paused_insert)
    monkeypatch.setattr(service._crud_repo, "check_unique", signalling_check_unique)

    with ThreadPoolExecutor(max_workers=2) as pool:
        reconcile_future = pool.submit(
            service.reconcile_paths, "ws", owner_id="u1", ws_path=str(workspace), paths=[relative]
        )
        assert reconcile_started.wait(timeout=5)

        save_future = pool.submit(
            service.save, "u1", "ws", str(workspace), "Concurrent", "body", []
        )
        assert not save_acquired.wait(timeout=0.1)

        release_reconcile.set()
        reconcile_future.result(timeout=5)
        save_future.result(timeout=5)

    assert save_acquired.is_set()
