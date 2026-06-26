from unittest.mock import patch

import pytest

from kajet_turbo import perf
from kajet_turbo.repositories.git import GitRepository


def test_save_perf_span_records_phases(service, workspace):
    with perf.perf_span() as span:
        service.save("u1", "ws", str(workspace), "Perf", "# Head\n\nbody text", [])
    # FTS-only test indexer => no embedding HTTP, but git/db/chunk phases are recorded.
    assert span.fields["git_ms"] > 0
    assert "git_lock_wait_ms" in span.fields
    assert "db_ms" in span.fields
    assert span.fields["chunks"] >= 1


def test_save_creates_file_and_db_record(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Testowa notatka", "treść", ["python"])
    assert "note_id" in result
    note_id = result["note_id"]
    assert (workspace / "Testowa notatka.md").exists()
    note = service._repo.get(note_id, owner_id="u1")
    assert note is not None
    assert note.title == "Testowa notatka"
    assert note.owner_id == "u1"


def test_save_in_root_does_not_create_notes_directory(service, workspace):
    service.save("u1", "ws", str(workspace), "Root note", "content", [])

    assert (workspace / "Root note.md").exists()
    assert not (workspace / "notes").exists()


def test_save_rejects_duplicate_title_in_same_folder(service, workspace):
    service.save("u1", "ws", str(workspace), "Duplicate", "content", [], folder="docs")

    with pytest.raises(ValueError):
        service.save("u1", "ws", str(workspace), "Duplicate", "other", [], folder="docs")


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
        service.update(
            note_id, owner_id="u1", ws_path=str(workspace), content="nowa treść", confirm=True
        )
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note["content"] == "stara treść"


def test_update_title_renames_file(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Old title", "content", [])["note_id"]

    service.update(note_id, owner_id="u1", ws_path=str(workspace), title="New title")

    assert not (workspace / "Old title.md").exists()
    assert (workspace / "New title.md").exists()


def test_update_append_mode_adds_to_section(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Dziennik", "## Zadania\n\n- Pierwsze", [])[
        "note_id"
    ]

    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        content="- Drugie",
        mode="append",
        target_heading="## Zadania",
    )

    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert "- Pierwsze\n- Drugie" in note["content"]
    # Edit produced a second commit (history grows).
    assert len(service.get_history(note_id, owner_id="u1", ws_path=str(workspace))) == 2


def test_update_replace_text_mode(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notatka", "Hello world.", [])["note_id"]

    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        content="earth",
        mode="replace_text",
        old_text="world",
    )

    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note["content"] == "Hello earth."


def test_update_insert_after_mode(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Lista", "- A\n- B\n", [])["note_id"]

    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        content="- A.5",
        mode="insert_after",
        old_text="- A",
    )

    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert "- A\n- A.5\n- B" in note["content"]


def test_update_edit_mode_requires_content(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notatka", "treść", [])["note_id"]

    with pytest.raises(ValueError, match="content"):
        service.update(
            note_id, owner_id="u1", ws_path=str(workspace), mode="append", target_heading=None
        )


def test_update_replace_text_ambiguous_raises(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notatka", "foo bar foo", [])["note_id"]

    with pytest.raises(ValueError):
        service.update(
            note_id,
            owner_id="u1",
            ws_path=str(workspace),
            content="qux",
            mode="replace_text",
            old_text="foo",
        )


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


def test_delete_removes_file_from_note_folder(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Delete me", "content", [], folder="trash")[
        "note_id"
    ]

    service.delete(note_id, owner_id="u1", ws_path=str(workspace))

    assert not (workspace / "trash" / "Delete me.md").exists()


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


def test_reindex_finds_notes_in_subfolders(service, workspace, note_file_factory):
    note_file_factory(workspace, "Root note", note_id="root-id")
    note_file_factory(workspace, "Nested note", note_id="nested-id", folder="docs")

    result = service.reindex("ws", owner_id="u1", ws_path=str(workspace))

    assert result["count"] == 2


def test_get_history_returns_commits(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Historia", "v1", [])
    note_id = result["note_id"]
    service.update(note_id, owner_id="u1", ws_path=str(workspace), content="v2", confirm=True)

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


def test_save_wikilink_in_code_is_not_validated(service, workspace):
    # `[[Ghost]]` inside inline code must not trigger validation.
    result = service.save("u1", "ws", str(workspace), "Source", "code `[[Ghost]]` here", [])
    assert "note_id" in result


def test_update_overwrite_broken_wikilink_rejected_keeps_content(service, workspace):
    from kajet_turbo.markdown import BrokenWikilinkError

    result = service.save("u1", "ws", str(workspace), "Note", "original", [])
    note_id = result["note_id"]
    with pytest.raises(BrokenWikilinkError):
        service.update(note_id, owner_id="u1", ws_path=str(workspace), content="[[Ghost]]")
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note["content"] == "original"


def test_update_append_mode_validates_after_apply_edit(service, workspace):
    from kajet_turbo.markdown import BrokenWikilinkError

    result = service.save("u1", "ws", str(workspace), "Note", "body", [])
    note_id = result["note_id"]
    with pytest.raises(BrokenWikilinkError):
        service.update(
            note_id, owner_id="u1", ws_path=str(workspace), content="[[Ghost]]", mode="append"
        )


def test_update_to_valid_wikilink_succeeds(service, workspace):
    service.save("u1", "ws", str(workspace), "Target", "t", [])
    result = service.save("u1", "ws", str(workspace), "Note", "body", [])
    note_id = result["note_id"]
    service.update(
        note_id, owner_id="u1", ws_path=str(workspace), content="link [[Target]]", confirm=True
    )
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert "[[Target]]" in note["content"]


def test_save_records_note_link(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "see [[Target]]", [])["note_id"]
    assert service._repo.backlinks(tid) == [sid]


def test_update_replaces_links(service, workspace):
    a = service.save("u1", "ws", str(workspace), "A", "a", [])["note_id"]
    b = service.save("u1", "ws", str(workspace), "B", "b", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[A]]", [])["note_id"]
    assert service._repo.backlinks(a) == [sid]
    service.update(sid, owner_id="u1", ws_path=str(workspace), content="now [[B]]", confirm=True)
    assert service._repo.backlinks(a) == []
    assert service._repo.backlinks(b) == [sid]


def test_delete_removes_outgoing_and_incoming_links(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    # Source -> Target edge exists; deleting Source clears the edge.
    service.delete(sid, owner_id="u1", ws_path=str(workspace))
    assert service._repo.backlinks(tid) == []


def test_delete_target_orphans_handled(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])
    service.delete(tid, owner_id="u1", ws_path=str(workspace))
    # Incoming edge to the deleted target is removed.
    assert service._repo.backlinks(tid) == []


def test_reindex_rebuilds_links(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    service.reindex("ws", "u1", str(workspace))
    assert service._repo.backlinks(tid) == [sid]


def test_move_rewrites_backlink_path(service, workspace):
    service.save("u1", "ws", str(workspace), "Target", "t", [], folder="Old")
    sid = service.save("u1", "ws", str(workspace), "Source", "see [[Old/Target|T]]", [])["note_id"]
    tid = service._repo.get_by_path("ws", "u1", "Old", "Target").id
    service.move(tid, owner_id="u1", ws_path=str(workspace), folder="New")
    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert "[[New/Target|T]]" in src["content"]
    assert "[[Old/Target" not in src["content"]
    # edge still points to the same target note
    assert service._repo.backlinks(tid) == [sid]


def test_rename_via_update_rewrites_backlink(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    service.update(tid, owner_id="u1", ws_path=str(workspace), title="Renamed")
    src = service.get_with_content(sid, owner_id="u1", ws_path=str(workspace))
    assert "[[Renamed]]" in src["content"]


def test_move_rewrite_creates_commit_in_source_history(service, workspace):
    service.save("u1", "ws", str(workspace), "Target", "t", [], folder="Old")
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Old/Target]]", [])["note_id"]
    tid = service._repo.get_by_path("ws", "u1", "Old", "Target").id
    service.move(tid, owner_id="u1", ws_path=str(workspace), folder="New")
    history = service.get_history(sid, owner_id="u1", ws_path=str(workspace))
    assert any("rewrite wikilink" in h["message"] for h in history)


def test_save_indexes_frontmatter_and_inline_tags(service, workspace):
    service.save(
        "u1",
        "ws",
        str(workspace),
        "Note",
        "body with #inline/tag here",
        ["Work/Projects"],
    )
    paths = {r["path"] for r in service._repo.tag_tree("ws", "u1")}
    assert paths == {"work", "work/projects", "inline", "inline/tag"}


def test_save_normalizes_frontmatter_tags_in_file(service, workspace):
    service.save("u1", "ws", str(workspace), "Note", "body", ["Work/Projects"])
    note_id = service._repo.list("ws", "u1", limit=None)[0]["note_id"]
    fetched = service.get(note_id, owner_id="u1")
    assert fetched["tags"] == ["work/projects"]  # normalized, frontmatter-only


def test_save_does_not_promote_inline_to_frontmatter(service, workspace):
    service.save("u1", "ws", str(workspace), "Note", "see #inline", [])
    note_id = service._repo.list("ws", "u1", limit=None)[0]["note_id"]
    assert service.get(note_id, owner_id="u1")["tags"] == []  # inline stays out of frontmatter


def test_update_resyncs_tags(service, workspace):
    res = service.save("u1", "ws", str(workspace), "Note", "body #old", ["keep"])
    service.update(
        res["note_id"], owner_id="u1", ws_path=str(workspace), content="body #new", confirm=True
    )
    paths = {r["path"] for r in service._repo.tag_tree("ws", "u1")}
    assert paths == {"keep", "new"}  # #old gone, #new added, frontmatter 'keep' stays


def test_delete_removes_tags(service, workspace):
    res = service.save("u1", "ws", str(workspace), "Note", "#x", ["y"])
    service.delete(res["note_id"], owner_id="u1", ws_path=str(workspace))
    assert service._repo.tag_tree("ws", "u1") == []


def test_tag_tree_and_notes_by_tag_service(service, workspace):
    service.save("u1", "ws", str(workspace), "A", "body", ["work/projects"])
    service.save("u1", "ws", str(workspace), "B", "body", ["work"])
    tree = service.tag_tree("ws", "u1")
    assert {t["path"] for t in tree} == {"work", "work/projects"}
    with_desc = service.notes_by_tag("ws", "u1", "work", include_descendants=True)
    assert {n["title"] for n in with_desc} == {"A", "B"}


def test_normalize_with_warnings_drops_invalid_and_dedups():
    from kajet_turbo.services.notes import NoteService

    out, warnings = NoteService._normalize_with_warnings(["Work", "work", "has space", "a/b"])
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
    assert set(note["tags"]) == {"python", "work"}


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
    assert note["tags"] == ["python"]


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

    result = service.set_tags(note_id, "u1", str(workspace), ["#Docs", "docs", "a b"], confirm=True)

    assert result["frontmatter_tags"] == ["docs"]  # normalized, deduped, invalid dropped
    assert len(result["warnings"]) == 1  # 'a b' warned
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note["tags"] == ["docs"]


def test_set_tags_requires_confirmation_when_dropping(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "treść", ["python", "work"])[
        "note_id"
    ]
    before = len(service.get_history(note_id, owner_id="u1", ws_path=str(workspace)))

    result = service.set_tags(note_id, "u1", str(workspace), ["docs"])

    assert result["requires_confirmation"] is True
    assert set(result["would_remove_tags"]) == {"python", "work"}
    assert result["overwrites_content"] is False
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert set(note["tags"]) == {"python", "work"}
    after = len(service.get_history(note_id, owner_id="u1", ws_path=str(workspace)))
    assert after == before


def test_set_tags_confirm_applies_drop(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "treść", ["python", "work"])[
        "note_id"
    ]

    result = service.set_tags(note_id, "u1", str(workspace), ["docs"], confirm=True)

    assert result.get("requires_confirmation") is None
    assert result["frontmatter_tags"] == ["docs"]


def test_set_tags_no_gate_when_superset(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "treść", ["python"])["note_id"]

    result = service.set_tags(note_id, "u1", str(workspace), ["python", "work"])

    assert result.get("requires_confirmation") is None
    assert set(result["frontmatter_tags"]) == {"python", "work"}


def test_update_requires_confirmation_on_content_overwrite(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "stara treść", [])["note_id"]
    before = len(service.get_history(note_id, owner_id="u1", ws_path=str(workspace)))

    result = service.update(note_id, owner_id="u1", ws_path=str(workspace), content="nowa treść")

    assert result["requires_confirmation"] is True
    assert result["overwrites_content"] is True
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note["content"] == "stara treść"
    after = len(service.get_history(note_id, owner_id="u1", ws_path=str(workspace)))
    assert after == before


def test_update_confirm_applies_content_overwrite(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "stara treść", [])["note_id"]

    service.update(
        note_id, owner_id="u1", ws_path=str(workspace), content="nowa treść", confirm=True
    )

    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note["content"] == "nowa treść"


def test_update_no_gate_on_empty_body_overwrite(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "", [])["note_id"]

    result = service.update(
        note_id, owner_id="u1", ws_path=str(workspace), content="pierwsza treść"
    )

    assert result.get("requires_confirmation") is None
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note["content"] == "pierwsza treść"


def test_update_no_gate_on_surgical_append(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "## H\n\n- a", [])["note_id"]

    result = service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        content="- b",
        mode="append",
        target_heading="## H",
    )

    assert result.get("requires_confirmation") is None


def test_update_requires_confirmation_on_tag_drop(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "treść", ["python", "work"])[
        "note_id"
    ]

    result = service.update(note_id, owner_id="u1", ws_path=str(workspace), tags=["python"])

    assert result["requires_confirmation"] is True
    assert result["would_remove_tags"] == ["work"]


# --- folder move / merge / prune ---


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


def test_validate_wikilinks_accepts_extra_targets(service, workspace):
    # No note "Target" exists in the DB; supply it via extra_targets.
    ids = service._validate_wikilinks(
        "ws", "u1", "see [[Target]]", extra_targets={("", "Target"): "abc1234"}
    )
    assert ids == {"abc1234"}


def test_validate_wikilinks_without_extra_still_raises(service, workspace):
    from kajet_turbo.markdown import BrokenWikilinkError

    with pytest.raises(BrokenWikilinkError):
        service._validate_wikilinks("ws", "u1", "see [[Nope]]")
