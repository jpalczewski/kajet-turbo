"""Tag indexing plus add/remove/set-tags/rename_tag coverage for NoteService."""

from dataclasses import replace
from unittest.mock import patch

import pytest

from kajet_turbo.markdown import EditSpec
from kajet_turbo.workspace import note_filepath, read_note_file, write_note_file
from tests.services.conftest import note_target, seed_user, workspace_target
from tests.services.helpers import corrupt_temporal_field, make_flaky_db_write


@pytest.fixture(autouse=True)
def _seed_default_owner(database):
    # rename_tag now enqueues reindex_note jobs (user_id FK to users.id).
    seed_user(database, "u1")


def test_save_indexes_frontmatter_and_inline_tags(service, workspace):
    service.save(
        workspace_target("u1", "ws", workspace),
        "Note",
        "body with #inline/tag here",
        ["Work/Projects"],
    )
    paths = {r["path"] for r in service._tag_repo.tag_tree("ws", "u1")}
    assert paths == {"work", "work/projects", "inline", "inline/tag"}


def test_save_normalizes_frontmatter_tags_in_file(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "Note", "body", ["Work/Projects"])
    note_id = service._crud_repo.list_notes("ws", "u1", limit=None)[0]["note_id"]
    fetched = service.get(note_id, owner_id="u1")
    assert fetched["tags"] == ["work/projects"]  # normalized, frontmatter-only


def test_save_does_not_promote_inline_to_frontmatter(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "Note", "see #inline", [])
    note_id = service._crud_repo.list_notes("ws", "u1", limit=None)[0]["note_id"]
    assert service.get(note_id, owner_id="u1")["tags"] == []  # inline stays out of frontmatter


def test_update_resyncs_tags(service, workspace):
    res = service.save(workspace_target("u1", "ws", workspace), "Note", "body #old", ["keep"])
    sha = service.get_history(note_target("u1", "ws", workspace, res["note_id"]))[0]["sha"]
    service.update(
        note_target("u1", "ws", workspace, res["note_id"]),
        expected_sha=sha,
        edit=EditSpec(content="body #new"),
    )
    paths = {r["path"] for r in service._tag_repo.tag_tree("ws", "u1")}
    assert paths == {"keep", "new"}  # #old gone, #new added, frontmatter 'keep' stays


def test_delete_removes_tags(service, workspace):
    res = service.save(workspace_target("u1", "ws", workspace), "Note", "#x", ["y"])
    service.delete(note_target("u1", "ws", workspace, res["note_id"]))
    assert service._tag_repo.tag_tree("ws", "u1") == []


def test_tag_tree_and_notes_by_tag_service(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "A", "body", ["work/projects"])
    service.save(workspace_target("u1", "ws", workspace), "B", "body", ["work"])
    tree = service.tag_tree("ws", "u1")
    assert {t["path"] for t in tree} == {"work", "work/projects"}
    with_desc = service.notes_by_tag("ws", "u1", "work", include_descendants=True)
    assert {n["title"] for n in with_desc} == {"A", "B"}


def test_normalize_with_warnings_drops_invalid_and_dedups():
    from kajet_turbo.services.notes import NoteTagService

    out, warnings = NoteTagService.normalize_with_warnings(["Work", "work", "has space", "a/b"])
    assert out == ["work", "a/b"]  # 'Work'/'work' unify, dedup; order kept
    assert len(warnings) == 1
    assert "has space" in warnings[0]


def test_add_tags_unions_into_frontmatter(service, workspace):
    note_id = service.save(workspace_target("u1", "ws", workspace), "Notka", "treść", ["python"])[
        "note_id"
    ]
    before = service._crud_repo.get(note_id, owner_id="u1")
    assert before is not None

    result = service.add_tags(note_target("u1", "ws", workspace, note_id), ["work", "python"])

    assert result["frontmatter_tags"] == ["python", "work"]  # existing kept, new appended, dedup
    assert set(result["tags"]) == {"python", "work"}
    assert result["warnings"] == []
    note = service.get_with_content(note_target("u1", "ws", workspace, note_id))
    assert set(note.tags) == {"python", "work"}
    after = service._crud_repo.get(note_id, owner_id="u1")
    assert after is not None
    assert after.index_generation == before.index_generation


def test_add_tags_keeps_db_occurred_at_when_file_value_is_corrupted(service, workspace):
    """A tag-only edit must not silently discard a note's occurred_at just because a
    hand-edit made the on-disk copy unparseable — it should fall back to the DB's
    last-known-good value (in both file and DB) instead of persisting the drop, and
    surface it as a warning (#132 follow-up)."""
    note_id = service.save(
        workspace_target("u1", "ws", workspace),
        "Corrupt Tag",
        "treść",
        [],
        occurred_at="2026-03-22",
    )["note_id"]
    path = note_filepath(str(workspace), "", "Corrupt Tag")
    corrupt_temporal_field(path, "occurred_at", "banana")

    result = service.add_tags(note_target("u1", "ws", workspace, note_id), ["work"])

    assert any("occurred_at" in w for w in result["warnings"])
    row = service._crud_repo.get(note_id, owner_id="u1")
    assert row is not None and row.occurred_at == "2026-03-22"
    after_meta, _ = read_note_file(path)
    assert after_meta.occurred_at == "2026-03-22"


def test_add_tags_preserves_hand_written_extras(service, workspace):
    """#105: _apply_tag_change only ever changed tags, but reconstructed the whole
    file from five scalars — any other frontmatter key was silently dropped."""
    note_id = service.save(workspace_target("u1", "ws", workspace), "Notka", "treść", ["python"])[
        "note_id"
    ]
    path = note_filepath(str(workspace), "", "Notka")
    meta, content = read_note_file(path)
    write_note_file(path, replace(meta, extras={"aliases": ["Old Name"]}), content)

    service.add_tags(note_target("u1", "ws", workspace, note_id), ["work"])

    after_meta, _ = read_note_file(path)
    assert after_meta.extras == {"aliases": ["Old Name"]}


def test_add_tags_idempotent_no_extra_commit(service, workspace):
    note_id = service.save(workspace_target("u1", "ws", workspace), "Notka", "treść", ["python"])[
        "note_id"
    ]
    before = len(service.get_history(note_target("u1", "ws", workspace, note_id)))

    result = service.add_tags(note_target("u1", "ws", workspace, note_id), ["python"])

    assert result["frontmatter_tags"] == ["python"]
    after = len(service.get_history(note_target("u1", "ws", workspace, note_id)))
    assert after == before  # no-op: identical list produced no new commit


def test_add_tags_includes_inline_in_effective(service, workspace):
    note_id = service.save(
        workspace_target("u1", "ws", workspace), "Notka", "body #inline here", []
    )["note_id"]

    result = service.add_tags(note_target("u1", "ws", workspace, note_id), ["work"])

    assert result["frontmatter_tags"] == ["work"]
    assert set(result["tags"]) == {"work", "inline"}  # effective = frontmatter union inline


def test_remove_tags_drops_from_frontmatter(service, workspace):
    note_id = service.save(
        workspace_target("u1", "ws", workspace), "Notka", "treść", ["python", "work"]
    )["note_id"]

    result = service.remove_tags(note_target("u1", "ws", workspace, note_id), ["work"])

    assert result["frontmatter_tags"] == ["python"]
    assert result["warnings"] == []
    note = service.get_with_content(note_target("u1", "ws", workspace, note_id))
    assert note.tags == ["python"]


def test_remove_absent_tag_is_noop(service, workspace):
    note_id = service.save(workspace_target("u1", "ws", workspace), "Notka", "treść", ["python"])[
        "note_id"
    ]
    before = len(service.get_history(note_target("u1", "ws", workspace, note_id)))

    result = service.remove_tags(note_target("u1", "ws", workspace, note_id), ["nope"])

    assert result["frontmatter_tags"] == ["python"]
    after = len(service.get_history(note_target("u1", "ws", workspace, note_id)))
    assert after == before


def test_remove_inline_only_tag_warns_and_keeps_it(service, workspace):
    note_id = service.save(workspace_target("u1", "ws", workspace), "Notka", "body #work here", [])[
        "note_id"
    ]
    before = len(service.get_history(note_target("u1", "ws", workspace, note_id)))

    result = service.remove_tags(note_target("u1", "ws", workspace, note_id), ["work"])

    # frontmatter had no 'work' -> no file change, but tag survives as inline
    assert result["frontmatter_tags"] == []
    assert "work" in result["tags"]
    assert any("work" in w and "#work" in w for w in result["warnings"])
    after = len(service.get_history(note_target("u1", "ws", workspace, note_id)))
    assert after == before


def test_set_tags_overwrites_frontmatter(service, workspace):
    note_id = service.save(
        workspace_target("u1", "ws", workspace), "Notka", "treść", ["python", "work"]
    )["note_id"]

    result = service.set_tags(note_target("u1", "ws", workspace, note_id), ["#Docs", "docs", "a b"])

    assert result["frontmatter_tags"] == ["docs"]  # normalized, deduped, invalid dropped
    assert len(result["warnings"]) == 1  # 'a b' warned
    note = service.get_with_content(note_target("u1", "ws", workspace, note_id))
    assert note.tags == ["docs"]


def test_set_tags_no_gate_when_superset(service, workspace):
    note_id = service.save(workspace_target("u1", "ws", workspace), "Notka", "treść", ["python"])[
        "note_id"
    ]

    result = service.set_tags(note_target("u1", "ws", workspace, note_id), ["python", "work"])

    assert set(result["frontmatter_tags"]) == {"python", "work"}


def test_apply_tag_change_db_failure_leaves_file_and_row_untouched(service, workspace):
    """#155: _apply_tag_change now writes its row before the git commit, inside one
    transaction that commits last — a DB-side failure must abort before either changes."""
    note_id = service.save(workspace_target("u1", "ws", workspace), "Notka", "treść", ["python"])[
        "note_id"
    ]
    sha = service.get_history(note_target("u1", "ws", workspace, note_id))[0]["sha"]
    flaky_update = make_flaky_db_write(service._crud_repo.update_in_session)

    with (
        patch.object(service._crud_repo, "update_in_session", flaky_update),
        pytest.raises(RuntimeError, match="db exploded"),
    ):
        service.add_tags(note_target("u1", "ws", workspace, note_id), ["work"])

    assert service.get_history(note_target("u1", "ws", workspace, note_id))[0]["sha"] == sha
    note = service.get_with_content(note_target("u1", "ws", workspace, note_id))
    assert note.tags == ["python"]


def _rename(service, workspace, old, new, **kw):
    return service.rename_tag(old, new, owner_id="u1", ws_name="ws", ws_path=str(workspace), **kw)


def _tag_paths(service) -> set[str]:
    return {row["path"] for row in service.tag_tree("ws", "u1")}


def test_rename_tag_moves_the_subtree_and_spares_lookalikes(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "A", "body", ["work", "work/projects"])
    service.save(workspace_target("u1", "ws", workspace), "B", "body", ["workflow"])
    result = _rename(service, workspace, "work", "job")
    assert result["renamed"] == 1
    paths = _tag_paths(service)
    assert paths == {"job", "job/projects", "workflow"}


def test_rename_tag_preserves_hand_written_extras(service, workspace):
    """#105: rename_tag builds its own frontmatter from five scalars, dropping any
    other key — pinned separately from _apply_tag_change's shared path."""
    service.save(workspace_target("u1", "ws", workspace), "A", "body", ["work"])
    path = note_filepath(str(workspace), "", "A")
    meta, content = read_note_file(path)
    write_note_file(path, replace(meta, extras={"aliases": ["Old A"]}), content)

    result = _rename(service, workspace, "work", "job")

    assert result["renamed"] == 1
    after_meta, _ = read_note_file(path)
    assert after_meta.extras == {"aliases": ["Old A"]}


def test_rename_tag_rewrites_inline_hashtags_so_the_old_tag_stays_gone(service, workspace):
    # Without the body rewrite, sync_tags would union '#cwiczenia' straight back in.
    saved = service.save(workspace_target("u1", "ws", workspace), "A", "patrz #cwiczenia tutaj", [])
    result = _rename(service, workspace, "cwiczenia", "ćwiczenia")
    assert result["inline_rewritten"] == 1
    note = service.get_with_content(note_target("u1", "ws", workspace, saved["note_id"]))
    assert note is not None
    assert "#ćwiczenia" in note.content
    assert _tag_paths(service) == {"ćwiczenia"}


def test_rename_tag_reindexes_only_notes_whose_body_changed(
    service, database, git_workspace_factory
):
    from kajet_turbo.repositories.jobs import JobRepository
    from tests.services.helpers import build_reindex_handler, drain_reindex_jobs

    # rename_tag only enqueues reindex_note now (chunking moved into the handler), so the
    # handler needs the note's real on-disk workspace root: <workspaces_dir>/u1/ws.
    workspace = git_workspace_factory("u1/ws")
    workspaces_dir = str(workspace.parent.parent)

    inline = service.save(workspace_target("u1", "ws", workspace), "A", "patrz #work tutaj", [])
    frontmatter = service.save(workspace_target("u1", "ws", workspace), "B", "body", ["work"])
    _rename(service, workspace, "work", "job")

    jobs = JobRepository(database.engine)
    handler = build_reindex_handler(database, workspaces_dir, jobs=jobs)
    drain_reindex_jobs(jobs, handler, "u1", "ws")

    rewritten = " ".join(c["content"] for c in service._chunk_repo.get_chunks(inline["note_id"]))
    assert "#job" in rewritten
    # The frontmatter-only note is not rechunked — tags never reach a chunk.
    untouched = " ".join(
        c["content"] for c in service._chunk_repo.get_chunks(frontmatter["note_id"])
    )
    assert untouched.strip() == "body"


def test_rename_tag_writes_one_commit_for_the_whole_workspace(service, workspace):
    a = service.save(workspace_target("u1", "ws", workspace), "A", "body", ["work"])
    b = service.save(workspace_target("u1", "ws", workspace), "B", "body", ["work"])
    _rename(service, workspace, "work", "job")
    head_a = service.get_history(note_target("u1", "ws", workspace, a["note_id"]))[0]
    head_b = service.get_history(note_target("u1", "ws", workspace, b["note_id"]))[0]
    assert head_a["sha"] == head_b["sha"]
    assert head_a["message"] == "tag: rename work -> job"


def test_rename_tag_onto_an_existing_tag_reports_a_conflict_and_changes_nothing(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "A", "body", ["osoba"])
    service.save(workspace_target("u1", "ws", workspace), "B", "body", ["osoby"])
    conflict = _rename(service, workspace, "osoba", "osoby")
    assert conflict["target"] == "osoby"
    assert (conflict["target_notes"], conflict["source_notes"]) == (1, 1)
    assert _tag_paths(service) == {"osoba", "osoby"}


def test_rename_tag_merges_when_asked(service, workspace):
    a = service.save(workspace_target("u1", "ws", workspace), "A", "body", ["osoba", "ludzie"])
    service.save(workspace_target("u1", "ws", workspace), "B", "body", ["osoby"])
    result = _rename(service, workspace, "osoba", "osoby", merge=True)
    assert (result["merged"], result["renamed"]) == (True, 1)
    assert service.get(a["note_id"], owner_id="u1")["tags"] == ["osoby", "ludzie"]
    assert _tag_paths(service) == {"osoby", "ludzie"}


def test_rename_tag_merge_dedupes_within_a_single_note(service, workspace):
    note = service.save(workspace_target("u1", "ws", workspace), "A", "body", ["osoba", "osoby"])
    _rename(service, workspace, "osoba", "osoby", merge=True)
    assert service.get(note["note_id"], owner_id="u1")["tags"] == ["osoby"]


def test_rename_tag_is_a_noop_when_nothing_moves(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "A", "body", ["work"])
    assert _rename(service, workspace, "work", "work")["renamed"] == 0


def test_rename_tag_rejects_an_unknown_tag(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "A", "body", ["work"])
    with pytest.raises(ValueError, match="nie istnieje"):
        _rename(service, workspace, "wrok", "job")


def test_rename_tag_rejects_moving_a_tag_into_its_own_subtree(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "A", "body", ["work"])
    with pytest.raises(ValueError, match="poddrzewa"):
        _rename(service, workspace, "work", "work/sub")


def test_rename_tag_rejects_an_invalid_target(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "A", "body", ["work"])
    with pytest.raises(ValueError, match="niepoprawny tag"):
        _rename(service, workspace, "work", "dwa slowa")


def test_rename_tag_restores_every_touched_file_when_a_write_fails(service, workspace, monkeypatch):
    from kajet_turbo.services.notes import service as service_module

    a = service.save(workspace_target("u1", "ws", workspace), "A", "body", ["work"])
    service.save(workspace_target("u1", "ws", workspace), "B", "body", ["work"])
    # A hand-written extra key must survive the rollback exactly like the tags do (#105).
    a_path = note_filepath(str(workspace), "", "A")
    a_meta, a_content = read_note_file(a_path)
    write_note_file(a_path, replace(a_meta, extras={"aliases": ["Old A"]}), a_content)
    head_before = service.get_history(note_target("u1", "ws", workspace, a["note_id"]))[0]["sha"]

    real_write = service_module.write_note_file
    calls = {"n": 0}

    def flaky_write(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # second note of the batch; restores come after and go through
            raise OSError("disk full")
        return real_write(*args, **kwargs)

    monkeypatch.setattr(service_module, "write_note_file", flaky_write)
    with pytest.raises(OSError, match="disk full"):
        _rename(service, workspace, "work", "job")

    monkeypatch.setattr(service_module, "write_note_file", real_write)
    for title in ("A", "B"):
        on_disk, _ = read_note_file(note_filepath(str(workspace), "", title))
        assert on_disk.tags == ["work"]
    a_meta_after, _ = read_note_file(a_path)
    assert a_meta_after.extras == {"aliases": ["Old A"]}
    assert service.get_history(note_target("u1", "ws", workspace, a["note_id"]))[0]["sha"] == (
        head_before
    )
    # #155: since this fix, the DB row is written and flushed *before* the failing file
    # write runs (write_rows precedes staged_workspace_change's apply phase inside
    # commit_rows_then_tree) — it reaches the DB and then rolls back with the transaction,
    # rather than never reaching it at all as it did under the old git-first ordering.
    assert service.get(a["note_id"], owner_id="u1")["tags"] == ["work"]


def test_rename_tag_db_failure_leaves_tree_and_all_rows_untouched(service, workspace):
    """#155: rename_tag's row writes are now batched into one transaction that commits
    last — a DB-side failure on note k must roll back every note's row, not just k's, and
    must never reach the git commit at all."""
    a = service.save(workspace_target("u1", "ws", workspace), "A", "body", ["work"])
    b = service.save(workspace_target("u1", "ws", workspace), "B", "body", ["work"])
    head_before = service.get_history(note_target("u1", "ws", workspace, a["note_id"]))[0]["sha"]

    flaky_update = make_flaky_db_write(service._crud_repo.update_in_session, fail_on_call=2)

    with (
        patch.object(service._crud_repo, "update_in_session", flaky_update),
        pytest.raises(RuntimeError, match="db exploded"),
    ):
        _rename(service, workspace, "work", "job")

    assert service.get_history(note_target("u1", "ws", workspace, a["note_id"]))[0]["sha"] == (
        head_before
    )
    assert service.get(a["note_id"], owner_id="u1")["tags"] == ["work"]
    assert service.get(b["note_id"], owner_id="u1")["tags"] == ["work"]
    for title in ("A", "B"):
        on_disk, _ = read_note_file(note_filepath(str(workspace), "", title))
        assert on_disk.tags == ["work"]


def test_rename_tag_join_table_sync_failure_rolls_back_the_whole_chunk(service, workspace):
    """#171: sync_note_tags_many_in_session runs inside write_rows now — the same
    transaction as the row update, committed before the same chunk's file write and git
    commit (commit_rows_then_tree runs write_rows, then StagedChange.apply(), then
    commit_changes, in that order) — instead of as a separate call after
    commit_rows_then_tree already returned. A failure there must roll back the row update
    and skip the file write and git commit entirely, not leave the file/row saying the new
    tag while note_tags (what note_ids_for_tags reads) still says the old one — the shape
    that made a note permanently unrepairable by a retry, since the retry's dedup check
    skips a note whose file already matches the target."""
    note_id = service.save(workspace_target("u1", "ws", workspace), "A", "body", ["work"])[
        "note_id"
    ]
    sha_before = service.get_history(note_target("u1", "ws", workspace, note_id))[0]["sha"]

    with (
        patch.object(
            service._tag_repo,
            "sync_note_tags_many_in_session",
            side_effect=RuntimeError("tag sync exploded"),
        ),
        pytest.raises(RuntimeError, match="tag sync exploded"),
    ):
        _rename(service, workspace, "work", "job")

    assert service.get_history(note_target("u1", "ws", workspace, note_id))[0]["sha"] == (
        sha_before
    )
    assert service.get(note_id, owner_id="u1")["tags"] == ["work"]
    on_disk, _ = read_note_file(note_filepath(str(workspace), "", "A"))
    assert on_disk.tags == ["work"]


def test_rename_tag_serializes_with_a_concurrent_tag_edit(service, workspace, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from kajet_turbo.services.notes import service as service_module

    note_id = service.save(workspace_target("u1", "ws", workspace), "A", "body #work", ["work"])[
        "note_id"
    ]
    rename_read = Event()
    release_rename = Event()
    real_rewrite = service_module.rewrite_inline_tags

    def paused_rewrite(*args, **kwargs):
        rename_read.set()
        assert release_rename.wait(timeout=2)
        return real_rewrite(*args, **kwargs)

    monkeypatch.setattr(service_module, "rewrite_inline_tags", paused_rewrite)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rename = pool.submit(_rename, service, workspace, "work", "job")
        assert rename_read.wait(timeout=2)
        add = pool.submit(service.add_tags, note_target("u1", "ws", workspace, note_id), ["extra"])
        assert not add.done()
        release_rename.set()
        rename.result(timeout=2)
        add.result(timeout=2)

    note = service.get_with_content(note_target("u1", "ws", workspace, note_id))
    assert note is not None
    assert note.tags == ["job", "extra"]
    assert "#job" in note.content


def test_rename_tag_chunks_large_batches_logging_note_ids_per_chunk(
    service, workspace, monkeypatch, capsys
):
    """#171/#173: above MAX_BATCH_COMMIT_SIZE, rename_tag splits into several
    commit_rows_then_tree calls (several transactions/git commits, not one), each logging
    its own repository_operation line with a note_ids field bounded to the chunk size —
    restoring the per-note traceability a single count=N line lost."""
    from kajet_turbo.log import setup_logging
    from kajet_turbo.services.notes import service as service_module
    from tests.helpers import entries_named, read_log_entries

    monkeypatch.setattr(service_module, "MAX_BATCH_COMMIT_SIZE", 2)
    setup_logging()
    note_ids = {
        service.save(workspace_target("u1", "ws", workspace), title, "body", ["work"])["note_id"]
        for title in ("A", "B", "C", "D", "E")
    }

    result = _rename(service, workspace, "work", "job")

    assert result["renamed"] == 5
    entries = entries_named(read_log_entries(capsys), "repository_operation")
    rename_ops = [e for e in entries if e.get("operation") == "notes.rename_tag"]
    assert len(rename_ops) == 3  # ceil(5/2)
    logged_ids: set[str] = set()
    for op in rename_ops:
        chunk_ids = op["note_ids"]
        assert len(chunk_ids) <= 2
        logged_ids.update(chunk_ids)
    assert logged_ids == note_ids
    assert _tag_paths(service) == {"job"}
    for note_id in note_ids:
        assert service.get(note_id, owner_id="u1")["tags"] == ["job"]
    # Chunking trades single-commit atomicity for bounded lock-hold time (#171): distinct
    # chunks land as distinct git commits, unlike the single-chunk case where every note's
    # most recent commit is the same sha.
    shas = {
        service.get_history(note_target("u1", "ws", workspace, note_id))[0]["sha"]
        for note_id in note_ids
    }
    assert len(shas) > 1


def test_rename_tag_resumes_after_a_mid_batch_chunk_failure(service, workspace, monkeypatch):
    """#171: a failure partway through a chunked rename only rolls back its own chunk —
    already-committed chunks stay renamed, and note_ids_for_tags (reading live join-table
    state) excludes them from a retry, so calling rename_tag again picks up exactly the
    unprocessed remainder instead of requiring the whole batch to be redone."""
    from kajet_turbo.services.notes import service as service_module

    monkeypatch.setattr(service_module, "MAX_BATCH_COMMIT_SIZE", 2)
    titles = ("A", "B", "C", "D")
    note_ids = {
        title: service.save(workspace_target("u1", "ws", workspace), title, "body", ["work"])[
            "note_id"
        ]
        for title in titles
    }
    # Two chunks of 2; fail partway through the second chunk's write (3rd note overall).
    flaky_update = make_flaky_db_write(service._crud_repo.update_in_session, fail_on_call=3)

    with (
        patch.object(service._crud_repo, "update_in_session", flaky_update),
        pytest.raises(RuntimeError, match="db exploded"),
    ):
        _rename(service, workspace, "work", "job")

    tags_by_title = {t: service.get(nid, owner_id="u1")["tags"] for t, nid in note_ids.items()}
    renamed_titles = {t for t, tags in tags_by_title.items() if tags == ["job"]}
    remaining_titles = {t for t, tags in tags_by_title.items() if tags == ["work"]}
    assert renamed_titles | remaining_titles == set(titles)
    assert renamed_titles  # the first chunk landed before the failure
    assert remaining_titles  # the failing chunk (and anything after) did not

    # The first chunk's notes already carry the target tag, so a retry must merge —
    # exactly the same conflict a caller would hit renaming onto any pre-existing tag.
    result = _rename(service, workspace, "work", "job", merge=True)

    assert result["renamed"] == len(remaining_titles)
    assert _tag_paths(service) == {"job"}
    for note_id in note_ids.values():
        assert service.get(note_id, owner_id="u1")["tags"] == ["job"]


def test_rename_tag_releases_workspace_before_reindexing(service, workspace, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    note_id = service.save(workspace_target("u1", "ws", workspace), "A", "body #work", ["work"])[
        "note_id"
    ]
    index_started = Event()
    release_index = Event()

    def paused_index(*_args) -> None:
        index_started.set()
        assert release_index.wait(timeout=5)

    monkeypatch.setattr(service._indexer, "index_many", paused_index)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rename = pool.submit(_rename, service, workspace, "work", "job")
        assert index_started.wait(timeout=5)
        try:
            add = pool.submit(
                service.add_tags, note_target("u1", "ws", workspace, note_id), ["extra"]
            )
            add.result(timeout=2)
        finally:
            release_index.set()
        rename.result(timeout=5)

    note = service.get_with_content(note_target("u1", "ws", workspace, note_id))
    assert note is not None
    assert note.tags == ["job", "extra"]
    assert "#job" in note.content
