"""Tag indexing plus add/remove/set-tags/rename_tag coverage for NoteService."""

import pytest

from kajet_turbo.workspace import note_filepath, read_note_file


def test_save_indexes_frontmatter_and_inline_tags(service, workspace):
    service.save(
        "u1",
        "ws",
        str(workspace),
        "Note",
        "body with #inline/tag here",
        ["Work/Projects"],
    )
    paths = {r["path"] for r in service._tag_repo.tag_tree("ws", "u1")}
    assert paths == {"work", "work/projects", "inline", "inline/tag"}


def test_save_normalizes_frontmatter_tags_in_file(service, workspace):
    service.save("u1", "ws", str(workspace), "Note", "body", ["Work/Projects"])
    note_id = service._crud_repo.list_notes("ws", "u1", limit=None)[0]["note_id"]
    fetched = service.get(note_id, owner_id="u1")
    assert fetched["tags"] == ["work/projects"]  # normalized, frontmatter-only


def test_save_does_not_promote_inline_to_frontmatter(service, workspace):
    service.save("u1", "ws", str(workspace), "Note", "see #inline", [])
    note_id = service._crud_repo.list_notes("ws", "u1", limit=None)[0]["note_id"]
    assert service.get(note_id, owner_id="u1")["tags"] == []  # inline stays out of frontmatter


def test_update_resyncs_tags(service, workspace):
    res = service.save("u1", "ws", str(workspace), "Note", "body #old", ["keep"])
    sha = service.get_history(res["note_id"], owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        res["note_id"],
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        content="body #new",
    )
    paths = {r["path"] for r in service._tag_repo.tag_tree("ws", "u1")}
    assert paths == {"keep", "new"}  # #old gone, #new added, frontmatter 'keep' stays


def test_delete_removes_tags(service, workspace):
    res = service.save("u1", "ws", str(workspace), "Note", "#x", ["y"])
    service.delete(res["note_id"], owner_id="u1", ws_path=str(workspace))
    assert service._tag_repo.tag_tree("ws", "u1") == []


def test_tag_tree_and_notes_by_tag_service(service, workspace):
    service.save("u1", "ws", str(workspace), "A", "body", ["work/projects"])
    service.save("u1", "ws", str(workspace), "B", "body", ["work"])
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
    note_id = service.save("u1", "ws", str(workspace), "Notka", "treść", ["python"])["note_id"]

    result = service.add_tags(note_id, "u1", str(workspace), ["work", "python"])

    assert result["frontmatter_tags"] == ["python", "work"]  # existing kept, new appended, dedup
    assert set(result["tags"]) == {"python", "work"}
    assert result["warnings"] == []
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert set(note.tags) == {"python", "work"}


def test_add_tags_idempotent_no_extra_commit(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "treść", ["python"])["note_id"]
    before = len(service.get_history(note_id, owner_id="u1", ws_path=str(workspace)))

    result = service.add_tags(note_id, "u1", str(workspace), ["python"])

    assert result["frontmatter_tags"] == ["python"]
    after = len(service.get_history(note_id, owner_id="u1", ws_path=str(workspace)))
    assert after == before  # no-op: identical list produced no new commit


def test_add_tags_includes_inline_in_effective(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "body #inline here", [])["note_id"]

    result = service.add_tags(note_id, "u1", str(workspace), ["work"])

    assert result["frontmatter_tags"] == ["work"]
    assert set(result["tags"]) == {"work", "inline"}  # effective = frontmatter union inline


def test_remove_tags_drops_from_frontmatter(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "treść", ["python", "work"])[
        "note_id"
    ]

    result = service.remove_tags(note_id, "u1", str(workspace), ["work"])

    assert result["frontmatter_tags"] == ["python"]
    assert result["warnings"] == []
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.tags == ["python"]


def test_remove_absent_tag_is_noop(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "treść", ["python"])["note_id"]
    before = len(service.get_history(note_id, owner_id="u1", ws_path=str(workspace)))

    result = service.remove_tags(note_id, "u1", str(workspace), ["nope"])

    assert result["frontmatter_tags"] == ["python"]
    after = len(service.get_history(note_id, owner_id="u1", ws_path=str(workspace)))
    assert after == before


def test_remove_inline_only_tag_warns_and_keeps_it(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "body #work here", [])["note_id"]
    before = len(service.get_history(note_id, owner_id="u1", ws_path=str(workspace)))

    result = service.remove_tags(note_id, "u1", str(workspace), ["work"])

    # frontmatter had no 'work' -> no file change, but tag survives as inline
    assert result["frontmatter_tags"] == []
    assert "work" in result["tags"]
    assert any("work" in w and "#work" in w for w in result["warnings"])
    after = len(service.get_history(note_id, owner_id="u1", ws_path=str(workspace)))
    assert after == before


def test_set_tags_overwrites_frontmatter(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "treść", ["python", "work"])[
        "note_id"
    ]

    result = service.set_tags(note_id, "u1", str(workspace), ["#Docs", "docs", "a b"])

    assert result["frontmatter_tags"] == ["docs"]  # normalized, deduped, invalid dropped
    assert len(result["warnings"]) == 1  # 'a b' warned
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.tags == ["docs"]


def test_set_tags_no_gate_when_superset(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "treść", ["python"])["note_id"]

    result = service.set_tags(note_id, "u1", str(workspace), ["python", "work"])

    assert set(result["frontmatter_tags"]) == {"python", "work"}


def _rename(service, workspace, old, new, **kw):
    return service.rename_tag(old, new, owner_id="u1", ws_name="ws", ws_path=str(workspace), **kw)


def _tag_paths(service) -> set[str]:
    return {row["path"] for row in service.tag_tree("ws", "u1")}


def test_rename_tag_moves_the_subtree_and_spares_lookalikes(service, workspace):
    service.save("u1", "ws", str(workspace), "A", "body", ["work", "work/projects"])
    service.save("u1", "ws", str(workspace), "B", "body", ["workflow"])
    result = _rename(service, workspace, "work", "job")
    assert result["renamed"] == 1
    paths = _tag_paths(service)
    assert paths == {"job", "job/projects", "workflow"}


def test_rename_tag_rewrites_inline_hashtags_so_the_old_tag_stays_gone(service, workspace):
    # Without the body rewrite, sync_tags would union '#cwiczenia' straight back in.
    saved = service.save("u1", "ws", str(workspace), "A", "patrz #cwiczenia tutaj", [])
    result = _rename(service, workspace, "cwiczenia", "ćwiczenia")
    assert result["inline_rewritten"] == 1
    note = service.get_with_content(saved["note_id"], "u1", str(workspace))
    assert note is not None
    assert "#ćwiczenia" in note.content
    assert _tag_paths(service) == {"ćwiczenia"}


def test_rename_tag_reindexes_only_notes_whose_body_changed(service, workspace):
    inline = service.save("u1", "ws", str(workspace), "A", "patrz #work tutaj", [])
    frontmatter = service.save("u1", "ws", str(workspace), "B", "body", ["work"])
    _rename(service, workspace, "work", "job")
    rewritten = " ".join(c["content"] for c in service._chunk_repo.get_chunks(inline["note_id"]))
    assert "#job" in rewritten
    # The frontmatter-only note is not rechunked — tags never reach a chunk.
    untouched = " ".join(
        c["content"] for c in service._chunk_repo.get_chunks(frontmatter["note_id"])
    )
    assert untouched.strip() == "body"


def test_rename_tag_writes_one_commit_for_the_whole_workspace(service, workspace):
    a = service.save("u1", "ws", str(workspace), "A", "body", ["work"])
    b = service.save("u1", "ws", str(workspace), "B", "body", ["work"])
    _rename(service, workspace, "work", "job")
    head_a = service.get_history(a["note_id"], owner_id="u1", ws_path=str(workspace))[0]
    head_b = service.get_history(b["note_id"], owner_id="u1", ws_path=str(workspace))[0]
    assert head_a["sha"] == head_b["sha"]
    assert head_a["message"] == "tag: rename work -> job"


def test_rename_tag_onto_an_existing_tag_reports_a_conflict_and_changes_nothing(service, workspace):
    service.save("u1", "ws", str(workspace), "A", "body", ["osoba"])
    service.save("u1", "ws", str(workspace), "B", "body", ["osoby"])
    conflict = _rename(service, workspace, "osoba", "osoby")
    assert conflict["target"] == "osoby"
    assert (conflict["target_notes"], conflict["source_notes"]) == (1, 1)
    assert _tag_paths(service) == {"osoba", "osoby"}


def test_rename_tag_merges_when_asked(service, workspace):
    a = service.save("u1", "ws", str(workspace), "A", "body", ["osoba", "ludzie"])
    service.save("u1", "ws", str(workspace), "B", "body", ["osoby"])
    result = _rename(service, workspace, "osoba", "osoby", merge=True)
    assert (result["merged"], result["renamed"]) == (True, 1)
    assert service.get(a["note_id"], owner_id="u1")["tags"] == ["osoby", "ludzie"]
    assert _tag_paths(service) == {"osoby", "ludzie"}


def test_rename_tag_merge_dedupes_within_a_single_note(service, workspace):
    note = service.save("u1", "ws", str(workspace), "A", "body", ["osoba", "osoby"])
    _rename(service, workspace, "osoba", "osoby", merge=True)
    assert service.get(note["note_id"], owner_id="u1")["tags"] == ["osoby"]


def test_rename_tag_is_a_noop_when_nothing_moves(service, workspace):
    service.save("u1", "ws", str(workspace), "A", "body", ["work"])
    assert _rename(service, workspace, "work", "work")["renamed"] == 0


def test_rename_tag_rejects_an_unknown_tag(service, workspace):
    service.save("u1", "ws", str(workspace), "A", "body", ["work"])
    with pytest.raises(ValueError, match="nie istnieje"):
        _rename(service, workspace, "wrok", "job")


def test_rename_tag_rejects_moving_a_tag_into_its_own_subtree(service, workspace):
    service.save("u1", "ws", str(workspace), "A", "body", ["work"])
    with pytest.raises(ValueError, match="poddrzewa"):
        _rename(service, workspace, "work", "work/sub")


def test_rename_tag_rejects_an_invalid_target(service, workspace):
    service.save("u1", "ws", str(workspace), "A", "body", ["work"])
    with pytest.raises(ValueError, match="niepoprawny tag"):
        _rename(service, workspace, "work", "dwa slowa")


def test_rename_tag_restores_every_touched_file_when_a_write_fails(service, workspace, monkeypatch):
    from kajet_turbo.services.notes import tags as tags_module

    a = service.save("u1", "ws", str(workspace), "A", "body", ["work"])
    service.save("u1", "ws", str(workspace), "B", "body", ["work"])
    head_before = service.get_history(a["note_id"], owner_id="u1", ws_path=str(workspace))[0]["sha"]

    real_write = tags_module.write_note_file
    calls = {"n": 0}

    def flaky_write(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # second note of the batch; restores come after and go through
            raise OSError("disk full")
        return real_write(*args, **kwargs)

    monkeypatch.setattr(tags_module, "write_note_file", flaky_write)
    with pytest.raises(OSError, match="disk full"):
        _rename(service, workspace, "work", "job")

    monkeypatch.setattr(tags_module, "write_note_file", real_write)
    # The files are the source of truth here — NoteData.tags reads the DB row, which the
    # aborted rename never reached.
    for title in ("A", "B"):
        on_disk = read_note_file(note_filepath(str(workspace), "", title))
        assert on_disk["tags"] == ["work"]
    assert service.get_history(a["note_id"], owner_id="u1", ws_path=str(workspace))[0]["sha"] == (
        head_before
    )
