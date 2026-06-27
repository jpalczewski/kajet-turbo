import pytest

from kajet_turbo.db import Database
from kajet_turbo.repositories.notes import NoteRepository, NoteTagRepository


@pytest.fixture
def note_repo(database: Database) -> NoteRepository:
    return NoteRepository(database.engine)


@pytest.fixture
def repo(database: Database) -> NoteTagRepository:
    return NoteTagRepository(database.engine)


def _insert_note(note_repo: NoteRepository, note_id: str, folder: str = "") -> None:
    note_repo.insert(note_id, "ws", "u1", f"Title {note_id}", [], "t", "t", "body", folder)


def test_sync_materializes_ancestors(note_repo: NoteRepository, repo: NoteTagRepository):
    _insert_note(note_repo, "n1")
    repo.sync_note_tags("n1", "ws", "u1", [("work/projects/client-a", "frontmatter")])
    paths = {row["path"] for row in repo.tag_tree("ws", "u1")}
    assert paths == {"work", "work/projects", "work/projects/client-a"}


def test_sync_single_tag_roundtrips(note_repo: NoteRepository, repo: NoteTagRepository):
    _insert_note(note_repo, "n1")
    repo.sync_note_tags("n1", "ws", "u1", [("work", "frontmatter")])
    rows = repo.notes_by_tag("ws", "u1", "work", include_descendants=False, limit=None)
    assert len(rows) == 1
    assert rows[0]["note_id"] == "n1"


def test_tag_tree_counts(note_repo: NoteRepository, repo: NoteTagRepository):
    _insert_note(note_repo, "n1")
    _insert_note(note_repo, "n2")
    repo.sync_note_tags("n1", "ws", "u1", [("work/projects", "frontmatter")])
    repo.sync_note_tags("n2", "ws", "u1", [("work", "frontmatter")])
    by_path = {r["path"]: r for r in repo.tag_tree("ws", "u1")}
    assert by_path["work"]["exact_count"] == 1
    assert by_path["work"]["descendant_count"] == 2
    assert by_path["work/projects"]["exact_count"] == 1
    assert by_path["work/projects"]["descendant_count"] == 1


def test_notes_by_tag_prefix_toggle(note_repo: NoteRepository, repo: NoteTagRepository):
    _insert_note(note_repo, "n1")
    _insert_note(note_repo, "n2")
    repo.sync_note_tags("n1", "ws", "u1", [("work/projects", "frontmatter")])
    repo.sync_note_tags("n2", "ws", "u1", [("work", "frontmatter")])
    with_desc = {r["note_id"] for r in repo.notes_by_tag("ws", "u1", "work", True, None)}
    exact = {r["note_id"] for r in repo.notes_by_tag("ws", "u1", "work", False, None)}
    assert with_desc == {"n1", "n2"}
    assert exact == {"n2"}


def test_resync_replaces_and_gcs_orphans(note_repo: NoteRepository, repo: NoteTagRepository):
    _insert_note(note_repo, "n1")
    repo.sync_note_tags("n1", "ws", "u1", [("work/projects", "frontmatter")])
    repo.sync_note_tags("n1", "ws", "u1", [("life", "frontmatter")])
    paths = {row["path"] for row in repo.tag_tree("ws", "u1")}
    assert paths == {"life"}


def test_underscore_prefix_not_overmatched(note_repo: NoteRepository, repo: NoteTagRepository):
    _insert_note(note_repo, "n1")
    _insert_note(note_repo, "n2")
    repo.sync_note_tags("n1", "ws", "u1", [("work_log", "frontmatter")])
    repo.sync_note_tags("n2", "ws", "u1", [("workxlog", "frontmatter")])
    got = {r["note_id"] for r in repo.notes_by_tag("ws", "u1", "work_log", True, None)}
    assert got == {"n1"}


def test_delete_note_tags_gcs(note_repo: NoteRepository, repo: NoteTagRepository):
    _insert_note(note_repo, "n1")
    repo.sync_note_tags("n1", "ws", "u1", [("work/projects", "frontmatter")])
    repo.delete_note_tags("n1", "ws", "u1")
    assert repo.tag_tree("ws", "u1") == []


def test_tag_counts_whole_workspace_sorted_by_popularity(
    note_repo: NoteRepository, repo: NoteTagRepository
):
    for nid in ("n1", "n2", "n3"):
        _insert_note(note_repo, nid)
    repo.sync_note_tags("n1", "ws", "u1", [("work", "frontmatter")])
    repo.sync_note_tags("n2", "ws", "u1", [("work", "frontmatter")])
    repo.sync_note_tags("n3", "ws", "u1", [("life", "frontmatter")])
    rows = repo.tag_counts("ws", "u1")
    # "work" (2 notes) before "life" (1) — sorted by count desc.
    assert [(r["path"], r["count"]) for r in rows] == [("work", 2), ("life", 1)]
    assert rows[0]["name"] == "work"


def test_tag_counts_exact_tag_only_no_descendant_rollup(
    note_repo: NoteRepository, repo: NoteTagRepository
):
    _insert_note(note_repo, "n1")
    repo.sync_note_tags("n1", "ws", "u1", [("work/projects", "frontmatter")])
    by_path = {r["path"]: r["count"] for r in repo.tag_counts("ws", "u1")}
    # Ancestor "work" exists as a node but carries no note directly → count 0 → omitted.
    assert by_path == {"work/projects": 1}


def test_tag_counts_folder_scope_toggles_subfolders(
    note_repo: NoteRepository, repo: NoteTagRepository
):
    _insert_note(note_repo, "n1", folder="Projekty")
    _insert_note(note_repo, "n2", folder="Projekty/Klient")
    _insert_note(note_repo, "n3", folder="Inne")
    repo.sync_note_tags("n1", "ws", "u1", [("a", "frontmatter")])
    repo.sync_note_tags("n2", "ws", "u1", [("b", "frontmatter")])
    repo.sync_note_tags("n3", "ws", "u1", [("c", "frontmatter")])

    recursive = {r["path"] for r in repo.tag_counts("ws", "u1", folder="Projekty")}
    assert recursive == {"a", "b"}  # subfolder Klient included

    exact = {
        r["path"] for r in repo.tag_counts("ws", "u1", folder="Projekty", include_subfolders=False)
    }
    assert exact == {"a"}  # only the folder itself


def test_tag_counts_empty_for_folder_without_notes(
    note_repo: NoteRepository, repo: NoteTagRepository
):
    _insert_note(note_repo, "n1", folder="Projekty")
    repo.sync_note_tags("n1", "ws", "u1", [("a", "frontmatter")])
    assert repo.tag_counts("ws", "u1", folder="Pusty") == []


def test_tag_counts_folder_underscore_not_overmatched(
    note_repo: NoteRepository, repo: NoteTagRepository
):
    # "_" is a LIKE wildcard; autoescape must keep "a_b" from matching "axb".
    _insert_note(note_repo, "n1", folder="a_b/sub")
    _insert_note(note_repo, "n2", folder="axb/sub")
    repo.sync_note_tags("n1", "ws", "u1", [("inside", "frontmatter")])
    repo.sync_note_tags("n2", "ws", "u1", [("outside", "frontmatter")])
    paths = {r["path"] for r in repo.tag_counts("ws", "u1", folder="a_b")}
    assert paths == {"inside"}


def test_list_tag_filter_is_prefix_aware(note_repo: NoteRepository, repo: NoteTagRepository):
    _insert_note(note_repo, "n1")
    _insert_note(note_repo, "n2")
    repo.sync_note_tags("n1", "ws", "u1", [("work/projects", "frontmatter")])
    repo.sync_note_tags("n2", "ws", "u1", [("life", "frontmatter")])
    got = {r["note_id"] for r in note_repo.list("ws", "u1", tags=["work"], limit=None, _tag_repo=repo)}
    assert got == {"n1"}  # matched via descendant work/projects
