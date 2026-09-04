"""Wikilink validation, conditional validation, and dangling-link write coverage."""

from unittest.mock import patch

import pytest

from kajet_turbo.markdown import BrokenWikilinkError, IndexedNote, render_markdown
from tests.services.conftest import seed_user
from tests.services.helpers import (
    corrupt_temporal_field,
    make_flaky_db_write,
    make_flaky_write,
    make_service_with_dangling,
)


@pytest.fixture(autouse=True)
def _seed_default_owner(database):
    # rewrite_backlinks now enqueues reindex_note jobs (user_id FK to users.id).
    seed_user(database, "u1")


def test_save_with_valid_wikilink_succeeds(service, workspace):
    service.save("u1", "ws", str(workspace), "Target", "treść", [], folder="A")
    result = service.save("u1", "ws", str(workspace), "Source", "see [[A/Target|t]]", [])
    assert "note_id" in result
    assert (workspace / "Source.md").exists()


def test_save_with_casefold_wikilink_succeeds(service, workspace):
    service.save("u1", "ws", str(workspace), "Target", "treść", [], folder="A")
    result = service.save("u1", "ws", str(workspace), "Source", "see [[a/target]]", [])
    assert "note_id" in result
    assert (workspace / "Source.md").exists()


def test_get_note_by_title_stays_exact_after_casefold_flip(service, workspace):
    service.save("u1", "ws", str(workspace), "Plan projektu", "cel", [])
    note = service.get_with_content_by_title("plan projektu", None, "u1", "ws", str(workspace))
    assert note is None


def test_save_with_broken_wikilink_rejected_and_no_file(service, workspace):
    with pytest.raises(BrokenWikilinkError) as exc:
        service.save("u1", "ws", str(workspace), "Source", "see [[Ghost]] and [[A/Nope]]", [])
    assert exc.value.broken == ["A/Nope", "Ghost"]
    assert not (workspace / "Source.md").exists()


def test_save_with_cross_workspace_link_succeeds(service, workspace):
    """[[note:ID]] never raises BrokenWikilinkError even when ID does not exist."""
    result = service.save(
        "u1", "ws1", str(workspace), "Source", "link to [[note:nonexistent-id-xyz]]", []
    )
    assert "note_id" in result


def test_save_with_cross_workspace_link_to_existing_note_records_edge(service, workspace):
    """[[note:ID]] where ID exists is stored in note_links."""
    target_id = service.save("u1", "ws2", str(workspace), "Target", "", [])["note_id"]
    source_id = service.save(
        "u1", "ws1", str(workspace), "Source", f"link to [[note:{target_id}]]", []
    )["note_id"]
    backlinks = service._link_service._link_repo.backlinks(target_id)
    assert source_id in backlinks


def test_save_cross_workspace_link_does_not_create_dangling(service, workspace):
    """[[note:nonexistent]] leaves no outgoing note_links row for source."""
    note_id = service.save("u1", "ws1", str(workspace), "Source", "[[note:ghost-id-000]]", [])[
        "note_id"
    ]
    outlinks = service._link_service._link_repo.outlinks(note_id)
    assert outlinks == []


def test_save_wikilink_in_code_is_not_validated(service, workspace):
    # `[[Ghost]]` inside inline code must not trigger validation.
    result = service.save("u1", "ws", str(workspace), "Source", "code `[[Ghost]]` here", [])
    assert "note_id" in result


def test_update_overwrite_broken_wikilink_rejected_keeps_content(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Note", "original", [])
    note_id = result["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    with pytest.raises(BrokenWikilinkError):
        service.update(
            note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha, content="[[Ghost]]"
        )
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.content == "original"


def test_update_append_mode_validates_after_apply_edit(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Note", "body", [])
    note_id = result["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    with pytest.raises(BrokenWikilinkError):
        service.update(
            note_id,
            owner_id="u1",
            ws_path=str(workspace),
            expected_sha=sha,
            content="[[Ghost]]",
            mode="append",
        )


def test_update_to_valid_wikilink_succeeds(service, workspace):
    service.save("u1", "ws", str(workspace), "Target", "t", [])
    result = service.save("u1", "ws", str(workspace), "Note", "body", [])
    note_id = result["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        content="link [[Target]]",
    )
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert "[[Target]]" in note.content


def test_save_records_note_link(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "see [[Target]]", [])["note_id"]
    assert service._link_service._link_repo.backlinks(tid) == [sid]


def test_update_replaces_links(service, workspace):
    a = service.save("u1", "ws", str(workspace), "A", "a", [])["note_id"]
    b = service.save("u1", "ws", str(workspace), "B", "b", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[A]]", [])["note_id"]
    assert service._link_service._link_repo.backlinks(a) == [sid]
    sha = service.get_history(sid, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        sid,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        content="now [[B]]",
    )
    assert service._link_service._link_repo.backlinks(a) == []
    assert service._link_service._link_repo.backlinks(b) == [sid]


def test_delete_removes_outgoing_and_incoming_links(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    # Source -> Target edge exists; deleting Source clears the edge.
    service.delete(sid, owner_id="u1", ws_path=str(workspace))
    assert service._link_service._link_repo.backlinks(tid) == []


def test_delete_target_orphans_handled(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])
    service.delete(tid, owner_id="u1", ws_path=str(workspace))
    # Incoming edge to the deleted target is removed.
    assert service._link_service._link_repo.backlinks(tid) == []


def test_reindex_rebuilds_links(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    service.reindex("ws", "u1", str(workspace))
    assert service._link_service._link_repo.backlinks(tid) == [sid]


def test_move_rewrites_backlink_path(service, workspace):
    service.save("u1", "ws", str(workspace), "Target", "t", [], folder="Old")
    sid = service.save("u1", "ws", str(workspace), "Source", "see [[Old/Target|T]]", [])["note_id"]
    tid = service._crud_repo.get_by_path("ws", "u1", "Old", "Target").id
    service.move(tid, owner_id="u1", ws_path=str(workspace), folder="New")
    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert "[[New/Target|T]]" in src.content
    assert "[[Old/Target" not in src.content
    # edge still points to the same target note
    assert service._link_service._link_repo.backlinks(tid) == [sid]


def test_move_rewrite_enqueues_one_reindex_note_job_per_rewritten_source(
    service, workspace, database
):
    """#89 acceptance criterion: rewrite_backlinks's search-reindex gap is closed by
    enqueuing a reindex_note job per rewritten source, in the same transaction as the row
    update — not by chunking inline (see NoteIndexer.index_many's contract change)."""
    import json

    from kajet_turbo.repositories.jobs import JobRepository

    service.save("u1", "ws", str(workspace), "Target", "t", [], folder="Old")
    sid_a = service.save("u1", "ws", str(workspace), "Source A", "see [[Old/Target|T]]", [])[
        "note_id"
    ]
    sid_b = service.save("u1", "ws", str(workspace), "Source B", "see [[Old/Target|T]]", [])[
        "note_id"
    ]
    tid = service._crud_repo.get_by_path("ws", "u1", "Old", "Target").id

    service.move(tid, owner_id="u1", ws_path=str(workspace), folder="New")

    jobs = JobRepository(database.engine).list_jobs("u1", kind="reindex_note", status="pending")
    note_ids = {json.loads(j.payload)["note_id"] for j in jobs}
    assert note_ids == {sid_a, sid_b}


def test_move_rewrite_leaves_source_outlinks_and_dangling_unchanged(database, workspace):
    """rewrite_backlinks() deliberately skips replace_links/write_dangling for the rewritten
    source note (see its docstring) — pin that the skip is actually harmless: the source's
    own outgoing-link graph and dangling-link bookkeeping are unaffected by the move."""
    svc, dangling = make_service_with_dangling(
        database, link_validation_enabled=lambda ws, owner: False
    )
    svc.save("u1", "ws", str(workspace), "Target", "t", [], folder="Old")
    svc.save("u1", "ws", str(workspace), "Ghost link", "irrelevant", [])
    sid = svc.save("u1", "ws", str(workspace), "Source", "see [[Old/Target|T]] and [[Nope]]", [])[
        "note_id"
    ]
    tid = svc._crud_repo.get_by_path("ws", "u1", "Old", "Target").id
    outlinks_before = sorted(svc._link_service._link_repo.outlinks(sid))
    dangling_before = dangling.list_for_workspace("u1", "ws")

    svc.move(tid, owner_id="u1", ws_path=str(workspace), folder="New")

    assert sorted(svc._link_service._link_repo.outlinks(sid)) == outlinks_before
    assert dangling.list_for_workspace("u1", "ws") == dangling_before


def test_rename_via_update_rewrites_backlink(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    sha = service.get_history(tid, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(tid, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="Renamed")
    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert "[[Renamed]]" in src.content


def test_rename_via_update_backlink_rewrite_preserves_source_extras(service, workspace):
    """#105: _rewrite_backlinks used to source tags/dates from the DB row after already
    reading (and discarding) the file — every rename dropped a linking note's custom
    frontmatter keys. It must now come from what was actually read."""
    from dataclasses import replace

    from kajet_turbo.workspace import note_filepath, read_note_file, write_note_file

    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    src_path = note_filepath(str(workspace), "", "Source")
    src_meta, src_content = read_note_file(src_path)
    write_note_file(src_path, replace(src_meta, extras={"aliases": ["Old Source"]}), src_content)

    sha = service.get_history(tid, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(tid, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="Renamed")

    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert "[[Renamed]]" in src.content
    after_meta, _ = read_note_file(src_path)
    assert after_meta.extras == {"aliases": ["Old Source"]}


def test_rewrite_backlinks_write_failing_partway_rolls_back_and_makes_no_commit(
    service, workspace, monkeypatch
):
    """_rewrite_backlinks gained a rollback with #104 (it previously had none at all, and
    committed once per source instead of once for the batch). Pin it: a write failing
    partway through the backlink batch leaves every source's wikilink text unrewritten —
    mirrors test_rename_tag_restores_every_touched_file_when_a_write_fails."""
    from kajet_turbo.services.notes import links as links_module

    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid_a = service.save("u1", "ws", str(workspace), "Source A", "[[Target]]", [])["note_id"]
    sid_b = service.save("u1", "ws", str(workspace), "Source B", "[[Target]]", [])["note_id"]
    sha = service.get_history(tid, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    real_write = links_module.write_note_file
    flaky_write = make_flaky_write(real_write)

    monkeypatch.setattr(links_module, "write_note_file", flaky_write)
    with pytest.raises(OSError, match="disk full"):
        service.update(
            tid, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="Renamed"
        )
    monkeypatch.setattr(links_module, "write_note_file", real_write)

    src_a = service.get_with_content(sid_a, owner_id="u1", ws_path=str(workspace))
    src_b = service.get_with_content(sid_b, owner_id="u1", ws_path=str(workspace))
    assert src_a.content == "[[Target]]"
    assert src_b.content == "[[Target]]"
    history_a = service.get_history(sid_a, owner_id="u1", ws_path=str(workspace))
    assert not any("rewrite wikilink" in h["message"] for h in history_a)


def test_move_rewrite_creates_commit_in_source_history(service, workspace):
    service.save("u1", "ws", str(workspace), "Target", "t", [], folder="Old")
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Old/Target]]", [])["note_id"]
    tid = service._crud_repo.get_by_path("ws", "u1", "Old", "Target").id
    service.move(tid, owner_id="u1", ws_path=str(workspace), folder="New")
    history = service.get_history(sid, owner_id="u1", ws_path=str(workspace))
    assert any("rewrite wikilink" in h["message"] for h in history)


def test_rewrite_backlinks_db_failure_leaves_backlink_untouched(service, workspace):
    """#155: _rewrite_backlinks now writes its row inside one transaction that commits
    last, same as every other write path. A DB-side failure on the backlink-rewrite step
    must not touch the linking note's file or row — and must not undo the rename that
    triggered it, a separate, already-committed transaction (update()'s own
    commit_rows_then_tree call)."""
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    sha = service.get_history(tid, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    src_sha_before = service.get_history(sid, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    # call 1: update()'s own row write (the rename) — must succeed and land.
    # call 2: _rewrite_backlinks' row write — fails.
    flaky_update = make_flaky_db_write(service._crud_repo.update_in_session, fail_on_call=2)

    with (
        patch.object(service._crud_repo, "update_in_session", flaky_update),
        pytest.raises(RuntimeError, match="db exploded"),
    ):
        service.update(
            tid, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="Renamed"
        )

    note = service.get_with_content(tid, owner_id="u1", ws_path=str(workspace))
    assert note.title == "Renamed"

    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert src.content == "[[Target]]"
    assert service.get_history(sid, owner_id="u1", ws_path=str(workspace))[0]["sha"] == (
        src_sha_before
    )


def test_rewrite_backlinks_git_failure_leaves_row_and_move_intact(service, workspace, monkeypatch):
    """Same shape as the DB-failure case above, but the git commit fails instead: #155's
    row-then-tree ordering applies to git-side failures too, not just DB-side ones."""
    from kajet_turbo.repositories.git import GitError, GitRepository

    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    sha = service.get_history(tid, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    src_sha_before = service.get_history(sid, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    # call 1: the rename itself; call 2: the backlink rewrite.
    flaky_commit_changes = make_flaky_db_write(
        GitRepository.commit_changes, fail_on_call=2, message="fail", exc=GitError
    )
    monkeypatch.setattr(GitRepository, "commit_changes", flaky_commit_changes)
    with pytest.raises(GitError, match="fail"):
        service.update(
            tid, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="Renamed"
        )

    note = service.get_with_content(tid, owner_id="u1", ws_path=str(workspace))
    assert note.title == "Renamed"

    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert src.content == "[[Target]]"
    assert service.get_history(sid, owner_id="u1", ws_path=str(workspace))[0]["sha"] == (
        src_sha_before
    )


def test_rewrite_backlinks_chunks_large_batches_logging_note_ids_per_chunk(
    service, workspace, monkeypatch, capsys
):
    """#171/#173: above MAX_BATCH_COMMIT_SIZE, _rewrite_backlinks splits into several
    commit_rows_then_tree calls instead of one unbounded batch, each logging its own
    repository_operation line with a note_ids field bounded to the chunk size."""
    from kajet_turbo.log import setup_logging
    from kajet_turbo.services.notes import links as links_module
    from tests.helpers import entries_named, read_log_entries

    monkeypatch.setattr(links_module, "MAX_BATCH_COMMIT_SIZE", 2)
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    source_ids = {
        service.save("u1", "ws", str(workspace), f"Source {i}", "[[Target]]", [])["note_id"]
        for i in range(5)
    }
    sha = service.get_history(tid, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    setup_logging()
    service.update(tid, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="Renamed")

    entries = entries_named(read_log_entries(capsys), "repository_operation")
    rewrite_ops = [e for e in entries if e.get("operation") == "notes.rewrite_backlinks"]
    assert len(rewrite_ops) == 3  # ceil(5/2)
    logged_ids: set[str] = set()
    for op in rewrite_ops:
        chunk_ids = op["note_ids"]
        assert len(chunk_ids) <= 2
        logged_ids.update(chunk_ids)
    assert logged_ids == source_ids
    for source_id in source_ids:
        src = service.get_with_content(source_id, owner_id="u1", ws_path=str(workspace))
        assert src.content == "[[Renamed]]"


def test_rewrite_backlinks_does_not_resync_occurred_at_period(service, workspace):
    """#125 (narrow): a backlink rewrite only ever changes wikilink text. It must not
    resync occurred_at/period from the file even when the file has drifted from the DB
    row since the last write through the service — that resync is reconcile's job."""
    from dataclasses import replace

    from kajet_turbo.workspace import note_filepath, read_note_file, write_note_file

    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save(
        "u1", "ws", str(workspace), "Source", "[[Target]]", [], occurred_at="2026-01-01"
    )["note_id"]

    src_path = note_filepath(str(workspace), "", "Source")
    src_meta, src_content = read_note_file(src_path)
    write_note_file(src_path, replace(src_meta, occurred_at="2099-12-31"), src_content)

    row_before = service._crud_repo.get(sid, owner_id="u1")
    generation_before = row_before.index_generation
    updated_at_before = row_before.updated_at

    sha = service.get_history(tid, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(tid, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="Renamed")

    row_after = service._crud_repo.get(sid, owner_id="u1")
    assert row_after.occurred_at == "2026-01-01"
    assert row_after.updated_at == updated_at_before
    assert row_after.index_generation == generation_before + 1


def test_rewrite_backlinks_heals_corrupted_occurred_at_instead_of_nulling_it(service, workspace):
    """#132 follow-up: a backlink rewrite reads the source file's frontmatter to preserve
    it verbatim (#105), so a hand-edit that made occurred_at unparseable must not turn
    into the rewrite silently writing `occurred_at: null` — it should fall back to the
    DB's (untouched, still correct) value instead, in both the file and the DB row."""
    from kajet_turbo.workspace import note_filepath, read_note_file

    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save(
        "u1", "ws", str(workspace), "Source", "[[Target]]", [], occurred_at="2026-01-01"
    )["note_id"]

    src_path = note_filepath(str(workspace), "", "Source")
    corrupt_temporal_field(src_path, "occurred_at", "banana")

    sha = service.get_history(tid, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(tid, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="Renamed")

    row_after = service._crud_repo.get(sid, owner_id="u1")
    assert row_after.occurred_at == "2026-01-01"
    src_meta, _ = read_note_file(src_path)
    assert src_meta.occurred_at == "2026-01-01"


def test_validate_wikilinks_accepts_extra_index_notes(service, workspace):
    # No note "Target" exists in the DB; supply it via the index's extra notes.
    workspace_links = service._link_service.for_workspace(
        "ws", "u1", extra=[IndexedNote("abc1234", "Batch", "Target")]
    )
    links = workspace_links.validate("see [[Target]]", "")
    assert links.resolved_ids == {"abc1234"}
    assert links.broken == []


def test_validate_wikilinks_without_extra_still_raises(service, workspace):
    with pytest.raises(BrokenWikilinkError):
        service._link_service.for_workspace("ws", "u1").validate("see [[Nope]]", "")


def test_with_extra_resolves_extra_notes_without_requerying(service, workspace, monkeypatch):
    base = service._link_service.for_workspace("ws", "u1")
    real_list_paths = service._crud_repo.list_paths
    calls = 0

    def counted_list_paths(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_list_paths(*args, **kwargs)

    monkeypatch.setattr(service._crud_repo, "list_paths", counted_list_paths)

    extended = base.with_extra([IndexedNote("abc1234", "Batch", "Target")])
    links = extended.validate("see [[Target]]", "")

    assert links.resolved_ids == {"abc1234"}
    assert links.broken == []
    assert calls == 0
    assert "abc1234" in {n.note_id for n in extended.paths}
    assert "abc1234" not in {n.note_id for n in base.paths}


# --- Obsidian-style short targets ---


def test_save_short_link_resolves_note_in_subfolder(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [], folder="Deep/Er")["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "see [[Target]]", [])["note_id"]
    assert service._link_service._link_repo.backlinks(tid) == [sid]


def test_save_suffix_path_resolves_nested_note(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [], folder="Deep/Er")["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "see [[Er/Target]]", [])["note_id"]
    assert service._link_service._link_repo.backlinks(tid) == [sid]


def test_save_ambiguous_short_link_prefers_source_folder(service, workspace):
    a = service.save("u1", "ws", str(workspace), "T", "a", [], folder="A")["note_id"]
    b = service.save("u1", "ws", str(workspace), "T", "b", [], folder="B")["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[T]]", [], folder="B")["note_id"]
    assert service._link_service._link_repo.backlinks(b) == [sid]
    assert service._link_service._link_repo.backlinks(a) == []


def test_save_many_short_links_between_batch_notes_in_folder(service, workspace):
    # Regression: notes saved together into a folder, linking each other by bare title,
    # used to fail validation because the in-batch targets were keyed by full path only.
    notes = [
        {"title": "Alpha", "content": "see [[Beta]]", "folder": "Proj/Docs"},
        {"title": "Beta", "content": "see [[Alpha]] and [[Gamma]]", "folder": "Proj/Docs"},
        {"title": "Gamma", "content": "see [[Docs/Alpha]]", "folder": "Proj/Docs"},
    ]
    results = service.save_many("u1", "ws", str(workspace), notes)
    assert all("note_id" in r for r in results), results
    ids = {n["title"]: r["note_id"] for n, r in zip(notes, results, strict=True)}
    links = service._link_service._link_repo
    assert set(links.outlinks(ids["Beta"])) == {ids["Alpha"], ids["Gamma"]}
    assert links.outlinks(ids["Gamma"]) == [ids["Alpha"]]


def test_rendered_short_link_points_at_target_folder(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [], folder="Deep/Er")["note_id"]
    html = render_markdown("[[Target]]", resolver=service.link_resolver("ws", "u1"), slug="ws")
    assert f'href="/workspace/ws/notes/Deep/Er/{tid}"' in html


def test_render_link_index_is_loaded_only_when_first_wikilink_is_rendered(
    service, workspace, monkeypatch
):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    calls = 0
    original = service._crud_repo.list_paths

    def counted_list_paths(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(service._crud_repo, "list_paths", counted_list_paths)
    resolver = service.link_resolver("ws", "u1")

    assert calls == 0
    render_markdown("plain text", resolver=resolver, slug="ws")
    assert calls == 0

    html = render_markdown("[[Target]] and [[Target]]", resolver=resolver, slug="ws")
    assert f'href="/workspace/ws/notes/{tid}"' in html
    assert calls == 1


def test_move_keeps_short_backlink_unchanged(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [], folder="Old")["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "see [[Target|T]]", [])["note_id"]
    service.move(tid, owner_id="u1", ws_path=str(workspace), folder="New")
    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert src.content == "see [[Target|T]]"
    assert service._link_service._link_repo.backlinks(tid) == [sid]


def test_rename_rewrites_short_backlink_to_short_new_title(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [], folder="Sub")["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    sha = service.get_history(tid, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(tid, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="Renamed")
    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert src.content == "[[Renamed]]"
    assert service._link_service._link_repo.backlinks(tid) == [sid]


def test_rename_falls_back_to_full_path_when_short_form_would_be_ambiguous(service, workspace):
    # Another "Renamed" at the root would capture a bare [[Renamed]] (exact-root rule), so
    # the rewrite must spell the full path to keep the link on the renamed note.
    service.save("u1", "ws", str(workspace), "Renamed", "decoy", [])
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [], folder="Sub")["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    sha = service.get_history(tid, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(tid, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="Renamed")
    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert src.content == "[[Sub/Renamed]]"
    assert service._link_service._link_repo.backlinks(tid) == [sid]


def test_move_rewrites_suffix_backlink_keeping_its_shape(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [], folder="A/Old")["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Old/Target]]", [])["note_id"]
    service.move(tid, owner_id="u1", ws_path=str(workspace), folder="A/New")
    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert src.content == "[[New/Target]]"


def test_move_folder_rewrites_source_linking_two_moved_notes_once(service, workspace):
    # One source links two notes in the moved folder: one rewrite commit, both links fixed.
    service.save("u1", "ws", str(workspace), "A", "a", [], folder="Old/Sub")
    service.save("u1", "ws", str(workspace), "B", "b", [], folder="Old/Sub")
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Old/Sub/A]] [[Old/Sub/B]]", [])[
        "note_id"
    ]
    before = len(service.get_history(sid, owner_id="u1", ws_path=str(workspace)))
    service.move_folder("Old", "New", owner_id="u1", ws_path=str(workspace), workspace="ws")
    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert src.content == "[[New/Sub/A]] [[New/Sub/B]]"
    assert len(service.get_history(sid, owner_id="u1", ws_path=str(workspace))) == before + 1


def test_move_to_root_rewrites_path_backlink_to_bare_title(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [], folder="Old")["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Old/Target|x]]", [])["note_id"]
    service.move(tid, owner_id="u1", ws_path=str(workspace), folder="")
    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert src.content == "[[Target|x]]"
    assert service._link_service._link_repo.backlinks(tid) == [sid]


def test_move_folder_ranks_co_moved_source_from_its_old_folder(service, workspace):
    # Source sits inside the moved folder and links [[T]], which pre-move meant Old/T (the
    # nearest T). After the move a decoy Dst/Old/Sub/T would win from the source's new
    # folder, so the rewrite must judge the link from where the source *was*.
    tid = service.save("u1", "ws", str(workspace), "T", "t", [], folder="Old")["note_id"]
    service.save("u1", "ws", str(workspace), "T", "decoy", [], folder="Dst/Old/Sub")
    sid = service.save("u1", "ws", str(workspace), "S", "[[T]]", [], folder="Old/Sub")["note_id"]
    assert service._link_service._link_repo.backlinks(tid) == [sid]
    service.move_folder("Old", "Dst/Old", owner_id="u1", ws_path=str(workspace), workspace="ws")
    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert src.content == "[[Old/T]]"
    assert service._link_service._link_repo.backlinks(tid) == [sid]


def test_reindex_resolves_short_links_and_xws_ids(service, workspace):
    from kajet_turbo.repositories.git import GitRepository

    other_ws = workspace.parent / "other"
    other_ws.mkdir()
    GitRepository.init(str(other_ws))
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [], folder="Deep")["note_id"]
    other = service.save("u1", "other", str(other_ws), "Far", "f", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", f"[[Target]] [[note:{other}]]", [])[
        "note_id"
    ]
    service.reindex("ws", "u1", str(workspace))
    assert service._link_service._link_repo.backlinks(tid) == [sid]
    assert service._link_service._link_repo.backlinks(other) == [sid]


# --- conditional link validation ---


def _make_service_with_validation(database, link_validation_enabled=None):
    """Build a NoteService with optional link_validation_enabled predicate."""
    from kajet_turbo.embedding.cache import EmbeddingCacheRepository
    from kajet_turbo.repositories.jobs import JobRepository
    from kajet_turbo.repositories.notes import NoteChunkRepository
    from tests.services.conftest import build_note_service

    chunk_repo = NoteChunkRepository(database.engine)
    from kajet_turbo.services.indexing import NoteIndexer

    indexer = NoteIndexer(
        chunk_repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda owner_id: None,
        jobs=JobRepository(database.engine),
    )
    return build_note_service(
        database, indexer=indexer, link_validation_enabled=link_validation_enabled
    )


def test_save_with_broken_wikilink_allowed_when_validation_disabled(database, workspace):
    """Validation disabled: broken [[Ghost]] does not raise; note is persisted."""
    svc = _make_service_with_validation(database, link_validation_enabled=lambda ws, owner: False)
    result = svc.save("u1", "ws", str(workspace), "Note A", "see [[Ghost]]", tags=[])
    assert "note_id" in result
    assert (workspace / "Note A.md").exists()
    assert svc._crud_repo.get(result["note_id"], owner_id="u1") is not None


def test_disabled_validation_still_links_existing_targets(database, workspace):
    """Validation disabled: resolved target IS in note_links; broken one is silently dropped."""
    svc = _make_service_with_validation(database, link_validation_enabled=lambda ws, owner: False)
    a = svc.save("u1", "ws", str(workspace), "Target", "body", tags=[])
    b = svc.save("u1", "ws", str(workspace), "Source", "[[Target]] and [[Ghost]]", tags=[])
    # Resolved target appears as a backlink; broken Ghost is absent.
    backlinks = svc._link_service._link_repo.backlinks(a["note_id"])
    assert b["note_id"] in backlinks
    # Ghost never existed, so no outlink edge for it (no error row either).
    outlinks = svc._link_service._link_repo.outlinks(b["note_id"])
    assert a["note_id"] in outlinks
    assert len(outlinks) == 1


def test_save_broken_wikilink_still_rejected_when_enabled_default(database, workspace):
    """Default (None predicate) keeps hard rejection — guards the existing contract."""
    svc = _make_service_with_validation(database)  # no predicate -> always enabled
    with pytest.raises(BrokenWikilinkError):
        svc.save("u1", "ws", str(workspace), "Note", "see [[Ghost]]", tags=[])


# --- dangling link writes ---


def test_validation_off_save_writes_dangling_rows(database, workspace):
    """Broken wikilinks on a validation-off save are persisted in dangling_links."""
    svc, dangling = make_service_with_dangling(
        database, link_validation_enabled=lambda ws, owner: False
    )
    res = svc.save("u1", "ws", str(workspace), "Source", "[[Ghost]] and [[Sub/Other]]", tags=[])
    rows = dangling.list_for_workspace("u1", "ws")
    assert {(r["target_folder"], r["target_title"]) for r in rows} == {
        ("", "Ghost"),
        ("Sub", "Other"),
    }
    assert all(r["source_note_id"] == res["note_id"] for r in rows)


def test_validation_off_resolved_link_writes_no_dangling(database, workspace):
    """Fully resolved wikilinks produce zero dangling rows."""
    svc, dangling = make_service_with_dangling(
        database, link_validation_enabled=lambda ws, owner: False
    )
    svc.save("u1", "ws", str(workspace), "Target", "body", tags=[])
    svc.save("u1", "ws", str(workspace), "Source", "[[Target]]", tags=[])
    assert dangling.exists("u1", "ws") is False


def test_resave_replaces_dangling_rows(database, workspace):
    """update() overwrites the source note's dangling rows, not appends."""
    svc, dangling = make_service_with_dangling(
        database, link_validation_enabled=lambda ws, owner: False
    )
    r = svc.save("u1", "ws", str(workspace), "Source", "[[Ghost]]", tags=[])
    sha = svc.get_history(r["note_id"], owner_id="u1", ws_path=str(workspace))[0]["sha"]
    svc.update(
        r["note_id"],
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        content="[[Other]]",
    )
    rows = dangling.list_for_workspace("u1", "ws")
    assert {(r2["target_folder"], r2["target_title"]) for r2 in rows} == {("", "Other")}


def test_validation_on_writes_no_dangling(database, workspace):
    """Validation-on raises BrokenWikilinkError before any dangling write."""
    svc, dangling = make_service_with_dangling(database)  # no predicate => validation ON
    with pytest.raises(BrokenWikilinkError):
        svc.save("u1", "ws", str(workspace), "Source", "[[Ghost]]", tags=[])
    assert dangling.exists("u1", "ws") is False


def test_delete_note_clears_dangling_rows(database, workspace):
    """Deleting a note that was the source of dangling links removes its dangling rows."""
    svc, dangling = make_service_with_dangling(
        database, link_validation_enabled=lambda ws, owner: False
    )
    res = svc.save("u1", "ws", str(workspace), "Source", "[[Ghost]]", tags=[])
    assert dangling.exists("u1", "ws") is True
    svc.delete(res["note_id"], "u1", str(workspace))
    assert dangling.exists("u1", "ws") is False
