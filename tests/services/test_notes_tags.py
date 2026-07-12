"""Tag indexing plus add/remove/set-tags happy-path coverage for NoteService."""


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
        confirm=True,
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

    result = service.set_tags(note_id, "u1", str(workspace), ["#Docs", "docs", "a b"], confirm=True)

    assert result["frontmatter_tags"] == ["docs"]  # normalized, deduped, invalid dropped
    assert len(result["warnings"]) == 1  # 'a b' warned
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.tags == ["docs"]


def test_set_tags_no_gate_when_superset(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "treść", ["python"])["note_id"]

    result = service.set_tags(note_id, "u1", str(workspace), ["python", "work"])

    assert result.get("requires_confirmation") is None
    assert set(result["frontmatter_tags"]) == {"python", "work"}
