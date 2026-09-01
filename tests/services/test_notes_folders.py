"""Folder move / merge / prune coverage for NoteService."""

import pytest


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
