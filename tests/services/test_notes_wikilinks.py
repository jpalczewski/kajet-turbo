"""Wikilink validation, conditional validation, and dangling-link write coverage."""

import pytest


def test_save_with_valid_wikilink_succeeds(service, workspace):
    service.save("u1", "ws", str(workspace), "Target", "treść", [], folder="A")
    result = service.save("u1", "ws", str(workspace), "Source", "see [[A/Target|t]]", [])
    assert "note_id" in result
    assert (workspace / "Source.md").exists()


def test_save_with_broken_wikilink_rejected_and_no_file(service, workspace):
    from kajet_turbo.markdown import BrokenWikilinkError

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
    from kajet_turbo.markdown import BrokenWikilinkError

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
    from kajet_turbo.markdown import BrokenWikilinkError

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


def test_rename_via_update_rewrites_backlink(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    sha = service.get_history(tid, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(tid, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="Renamed")
    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert "[[Renamed]]" in src.content


def test_move_rewrite_creates_commit_in_source_history(service, workspace):
    service.save("u1", "ws", str(workspace), "Target", "t", [], folder="Old")
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Old/Target]]", [])["note_id"]
    tid = service._crud_repo.get_by_path("ws", "u1", "Old", "Target").id
    service.move(tid, owner_id="u1", ws_path=str(workspace), folder="New")
    history = service.get_history(sid, owner_id="u1", ws_path=str(workspace))
    assert any("rewrite wikilink" in h["message"] for h in history)


def test_validate_wikilinks_accepts_extra_targets(service, workspace):
    # No note "Target" exists in the DB; supply it via extra_targets.
    ids, broken = service._link_service.validate_wikilinks(
        "ws", "u1", "see [[Target]]", extra_targets={("", "Target"): "abc1234"}
    )
    assert ids == {"abc1234"}
    assert broken == []


def test_validate_wikilinks_without_extra_still_raises(service, workspace):
    from kajet_turbo.markdown import BrokenWikilinkError

    with pytest.raises(BrokenWikilinkError):
        service._link_service.validate_wikilinks("ws", "u1", "see [[Nope]]")


# --- conditional link validation ---


def _make_service_with_validation(database, link_validation_enabled=None):
    """Build a NoteService with optional link_validation_enabled predicate."""
    from kajet_turbo.embedding.cache import EmbeddingCacheRepository
    from kajet_turbo.repositories.notes import NoteChunkRepository
    from tests.services.conftest import build_note_service

    chunk_repo = NoteChunkRepository(database.engine)
    from kajet_turbo.services.indexing import NoteIndexer

    indexer = NoteIndexer(
        chunk_repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda owner_id: None,
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
    from kajet_turbo.markdown import BrokenWikilinkError

    svc = _make_service_with_validation(database)  # no predicate -> always enabled
    with pytest.raises(BrokenWikilinkError):
        svc.save("u1", "ws", str(workspace), "Note", "see [[Ghost]]", tags=[])


# --- dangling link writes ---


def _make_service_with_dangling(database, link_validation_enabled=None):
    """Build a NoteService wired with a real DanglingLinkRepository on the same engine."""
    from kajet_turbo.embedding.cache import EmbeddingCacheRepository
    from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
    from kajet_turbo.repositories.notes import NoteChunkRepository
    from kajet_turbo.services.indexing import NoteIndexer
    from tests.services.conftest import build_note_service

    chunk_repo = NoteChunkRepository(database.engine)
    indexer = NoteIndexer(
        chunk_repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda owner_id: None,
    )
    dangling = DanglingLinkRepository(database.engine)
    return (
        build_note_service(
            database,
            indexer=indexer,
            link_validation_enabled=link_validation_enabled,
            dangling_repo=dangling,
        ),
        dangling,
    )


def test_validation_off_save_writes_dangling_rows(database, workspace):
    """Broken wikilinks on a validation-off save are persisted in dangling_links."""
    svc, dangling = _make_service_with_dangling(
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
    svc, dangling = _make_service_with_dangling(
        database, link_validation_enabled=lambda ws, owner: False
    )
    svc.save("u1", "ws", str(workspace), "Target", "body", tags=[])
    svc.save("u1", "ws", str(workspace), "Source", "[[Target]]", tags=[])
    assert dangling.exists("u1", "ws") is False


def test_resave_replaces_dangling_rows(database, workspace):
    """update() overwrites the source note's dangling rows, not appends."""
    svc, dangling = _make_service_with_dangling(
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
    from kajet_turbo.markdown import BrokenWikilinkError

    svc, dangling = _make_service_with_dangling(database)  # no predicate => validation ON
    with pytest.raises(BrokenWikilinkError):
        svc.save("u1", "ws", str(workspace), "Source", "[[Ghost]]", tags=[])
    assert dangling.exists("u1", "ws") is False


def test_delete_note_clears_dangling_rows(database, workspace):
    """Deleting a note that was the source of dangling links removes its dangling rows."""
    svc, dangling = _make_service_with_dangling(
        database, link_validation_enabled=lambda ws, owner: False
    )
    res = svc.save("u1", "ws", str(workspace), "Source", "[[Ghost]]", tags=[])
    assert dangling.exists("u1", "ws") is True
    svc.delete(res["note_id"], "u1", str(workspace))
    assert dangling.exists("u1", "ws") is False
