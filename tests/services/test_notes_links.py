"""links()/backlinks() and xws_link_resolver() coverage for NoteService."""

from sqlalchemy import insert, text

from kajet_turbo.models import NoteLink


def test_links_returns_outlinks_and_backlinks(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "content", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "see [[Target]]", [])["note_id"]
    result = service.links(tid, "u1")
    assert result is not None
    assert result["backlinks"] == [
        {"note_id": sid, "title": "Source", "folder": "", "workspace": "ws"}
    ]
    assert result["outlinks"] == []


def test_links_outlinks_populated(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "content", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "see [[Target]]", [])["note_id"]
    result = service.links(sid, "u1")
    assert result is not None
    assert result["outlinks"] == [
        {"note_id": tid, "title": "Target", "folder": "", "workspace": "ws"}
    ]
    assert result["backlinks"] == []


def test_links_empty_when_no_links(service, workspace):
    nid = service.save("u1", "ws", str(workspace), "Lonely", "no links here", [])["note_id"]
    result = service.links(nid, "u1")
    assert result == {"outlinks": [], "backlinks": []}


def test_links_returns_none_for_unknown_note(service, workspace):
    assert service.links("nonexistent-id", "u1") is None


def test_links_returns_none_for_wrong_owner(service, workspace):
    nid = service.save("u1", "ws", str(workspace), "Note", "content", [])["note_id"]
    assert service.links(nid, "u2") is None


def test_links_orphaned_target_skipped(service, database, workspace):
    # Save source and target, establish a real NoteLink row
    tid = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    # Bypass FK constraints by directly inserting a stale NoteLink row to a nonexistent target
    # (simulating a race condition where a target was deleted but link row remains)
    orphan_id = "orphaned-target-id"
    engine = database.engine

    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(
            insert(NoteLink).values(
                source_note_id=sid,
                target_note_id=orphan_id,
                workspace="ws",
                owner_id="u1",
            )
        )
        conn.execute(text("PRAGMA foreign_keys=ON"))
    # _resolve_link_notes must skip the stale edge (get() returns None for orphan_id)
    result = service.links(sid, "u1")
    assert result is not None
    # Valid link to Target + stale link to orphan should result in one outlink (orphan skipped)
    assert len(result["outlinks"]) == 1
    assert result["outlinks"][0]["note_id"] == tid


def test_links_include_meta_adds_tags_and_updated_at(service, workspace):
    tid = service.save("u1", "ws", str(workspace), "Target", "t", ["work"])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    result = service.links(sid, "u1", include_meta=True)
    assert result is not None
    entry = result["outlinks"][0]
    assert entry["note_id"] == tid
    assert entry["title"] == "Target"
    assert entry["folder"] == ""
    assert "tags" in entry
    assert entry["tags"] == ["work"]
    assert "updated_at" in entry


def test_links_default_excludes_meta(service, workspace):
    service.save("u1", "ws", str(workspace), "Target", "t", ["work"])["note_id"]
    sid = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    result = service.links(sid, "u1")
    assert result is not None
    entry = result["outlinks"][0]
    assert "tags" not in entry
    assert "updated_at" not in entry


def test_backlinks_include_cross_workspace_by_default(service, workspace):
    target_id = service.save("u1", "ws-b", str(workspace), "Target", "", [])["note_id"]
    source_id = service.save("u1", "ws-a", str(workspace), "Source", f"[[note:{target_id}]]", [])[
        "note_id"
    ]
    result = service.links(target_id, owner_id="u1")
    assert result is not None
    assert any(b["note_id"] == source_id for b in result["backlinks"])


def test_backlinks_exclude_cross_workspace_when_flag_false(service, workspace):
    target_id = service.save("u1", "ws-b", str(workspace), "Target", "", [])["note_id"]
    service.save("u1", "ws-a", str(workspace), "Source", f"[[note:{target_id}]]", [])
    result = service.links(target_id, owner_id="u1", include_cross_workspace=False)
    assert result is not None
    assert result["backlinks"] == []


def test_link_item_includes_workspace_field(service, workspace):
    target_id = service.save("u1", "ws-b", str(workspace), "Target", "", [])["note_id"]
    service.save("u1", "ws-a", str(workspace), "Source", f"[[note:{target_id}]]", [])
    result = service.links(target_id, owner_id="u1")
    assert result is not None
    backlink = result["backlinks"][0]
    assert "workspace" in backlink
    assert backlink["workspace"] == "ws-a"


def test_rename_does_not_rewrite_cross_workspace_backlink(service, workspace):
    # ws-b note is the target; ws-a note links to it via [[note:ID]] (cross-workspace syntax).
    target_id = service.save("u1", "ws-b", str(workspace), "Old Title", "content", [])["note_id"]
    source_id = service.save("u1", "ws-a", str(workspace), "Linker", f"[[note:{target_id}]]", [])[
        "note_id"
    ]

    # Rename the ws-b note — rewrite_backlinks must not touch the ws-a file.
    sha = service.get_history(target_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        target_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha, title="New Title"
    )

    source = service.get_with_content(source_id, owner_id="u1", ws_path=str(workspace))
    assert source is not None
    # The cross-workspace link is ID-stable: content must be unchanged.
    assert f"[[note:{target_id}]]" in source.content


# --- xws_link_resolver ---


def test_xws_link_resolver_returns_title_and_url(service, workspace):
    result = service.save("u1", "myws", str(workspace), "The Note", "", [])
    note_id = result["note_id"]
    resolver = service.xws_link_resolver("u1")
    resolution = resolver(note_id)
    assert resolution is not None
    title, url = resolution
    assert title == "The Note"
    assert f"/workspace/myws/notes/{note_id}" in url


def test_xws_link_resolver_returns_none_for_missing(service, workspace):
    resolver = service.xws_link_resolver("u1")
    assert resolver("nonexistent-id") is None


def test_xws_link_resolver_encodes_folder_segments(service, workspace):
    result = service.save("u1", "myws", str(workspace), "Deep Note", "", [], folder="docs/sub")
    note_id = result["note_id"]
    resolver = service.xws_link_resolver("u1")
    resolution = resolver(note_id)
    assert resolution is not None
    _title, url = resolution
    assert f"/workspace/myws/notes/docs/sub/{note_id}" in url


def test_xws_link_resolver_wrong_owner_returns_none(service, workspace):
    result = service.save("u1", "myws", str(workspace), "Owned Note", "", [])
    note_id = result["note_id"]
    resolver = service.xws_link_resolver("u2")  # different owner
    assert resolver(note_id) is None
