from kajet_turbo.markdown import IndexedNote
from kajet_turbo.services.notes.paths import (
    build_path_index,
    find_path_collisions,
    note_path_conflict,
)
from kajet_turbo.workspace import note_filepath

WS_PATH = "/workspaces/u1/ws"


def test_note_path_conflict_finds_normalization_collision():
    paths = [IndexedNote("n1", "", "A B")]

    conflict = note_path_conflict(paths, WS_PATH, "", "A:B")

    assert conflict is not None
    assert conflict.note_id == "n1"


def test_note_path_conflict_none_for_different_path():
    paths = [IndexedNote("n1", "", "A B")]

    assert note_path_conflict(paths, WS_PATH, "", "Something Else") is None


def test_note_path_conflict_respects_exclude_id():
    paths = [IndexedNote("n1", "", "A B")]

    assert note_path_conflict(paths, WS_PATH, "", "A:B", exclude_id="n1") is None


def test_note_path_conflict_exclude_id_is_id_scoped_not_title_scoped():
    """exclude_id must skip only the note with that exact id — a second, different note
    that happens to collide via the same computed path must still be caught."""
    paths = [IndexedNote("n1", "", "A B"), IndexedNote("n2", "", "A B")]

    conflict = note_path_conflict(paths, WS_PATH, "", "A:B", exclude_id="n1")

    assert conflict is not None
    assert conflict.note_id == "n2"


def test_note_path_conflict_empty_paths():
    assert note_path_conflict([], WS_PATH, "", "A B") is None


def test_build_path_index_matches_note_path_conflict():
    """The O(1) index and the O(n) predicate must agree on the same input."""
    paths = [IndexedNote("n1", "", "A B"), IndexedNote("n2", "sub", "Other")]

    index = build_path_index(paths, WS_PATH)

    assert index[note_filepath(WS_PATH, "", "A:B")].note_id == "n1"
    assert note_filepath(WS_PATH, "sub", "Other") in index
    assert index.get(note_filepath(WS_PATH, "", "Nope")) is None


def test_find_path_collisions_groups_only_colliding_paths():
    paths = [
        IndexedNote("n1", "", "A B"),
        IndexedNote("n2", "", "A:B"),
        IndexedNote("n3", "", "Solo"),
    ]

    collisions = find_path_collisions(paths, WS_PATH)

    assert len(collisions) == 1
    (group,) = collisions.values()
    assert {n.note_id for n in group} == {"n1", "n2"}
