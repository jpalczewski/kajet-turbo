from kajet_turbo.markdown import IndexedNote
from kajet_turbo.services.notes.paths import find_path_collisions, note_path_conflict

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


def test_note_path_conflict_empty_paths():
    assert note_path_conflict([], WS_PATH, "", "A B") is None


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
