import pytest

from kajet_turbo.db import Database
from kajet_turbo.repositories.notes import NoteRepository


@pytest.fixture
def repo(database: Database) -> NoteRepository:
    return NoteRepository(database.engine)


def _insert_note(repo: NoteRepository, note_id: str) -> None:
    repo.insert(note_id, "ws", "u1", f"Title {note_id}", [], "t", "t", "body", "")


def test_sync_materializes_ancestors(repo: NoteRepository):
    _insert_note(repo, "n1")
    repo.sync_note_tags("n1", "ws", "u1", [("work/projects/client-a", "frontmatter")])
    paths = {row["path"] for row in repo.tag_tree("ws", "u1")}
    assert paths == {"work", "work/projects", "work/projects/client-a"}


def test_sync_single_tag_roundtrips(repo: NoteRepository):
    _insert_note(repo, "n1")
    repo.sync_note_tags("n1", "ws", "u1", [("work", "frontmatter")])
    rows = repo.notes_by_tag("ws", "u1", "work", include_descendants=False, limit=None)
    assert len(rows) == 1
    assert rows[0]["note_id"] == "n1"


def test_tag_tree_counts(repo: NoteRepository):
    _insert_note(repo, "n1")
    _insert_note(repo, "n2")
    repo.sync_note_tags("n1", "ws", "u1", [("work/projects", "frontmatter")])
    repo.sync_note_tags("n2", "ws", "u1", [("work", "frontmatter")])
    by_path = {r["path"]: r for r in repo.tag_tree("ws", "u1")}
    assert by_path["work"]["exact_count"] == 1
    assert by_path["work"]["descendant_count"] == 2
    assert by_path["work/projects"]["exact_count"] == 1
    assert by_path["work/projects"]["descendant_count"] == 1


def test_notes_by_tag_prefix_toggle(repo: NoteRepository):
    _insert_note(repo, "n1")
    _insert_note(repo, "n2")
    repo.sync_note_tags("n1", "ws", "u1", [("work/projects", "frontmatter")])
    repo.sync_note_tags("n2", "ws", "u1", [("work", "frontmatter")])
    with_desc = {r["note_id"] for r in repo.notes_by_tag("ws", "u1", "work", True, None)}
    exact = {r["note_id"] for r in repo.notes_by_tag("ws", "u1", "work", False, None)}
    assert with_desc == {"n1", "n2"}
    assert exact == {"n2"}


def test_resync_replaces_and_gcs_orphans(repo: NoteRepository):
    _insert_note(repo, "n1")
    repo.sync_note_tags("n1", "ws", "u1", [("work/projects", "frontmatter")])
    repo.sync_note_tags("n1", "ws", "u1", [("life", "frontmatter")])
    paths = {row["path"] for row in repo.tag_tree("ws", "u1")}
    assert paths == {"life"}


def test_underscore_prefix_not_overmatched(repo: NoteRepository):
    _insert_note(repo, "n1")
    _insert_note(repo, "n2")
    repo.sync_note_tags("n1", "ws", "u1", [("work_log", "frontmatter")])
    repo.sync_note_tags("n2", "ws", "u1", [("workxlog", "frontmatter")])
    got = {r["note_id"] for r in repo.notes_by_tag("ws", "u1", "work_log", True, None)}
    assert got == {"n1"}


def test_delete_note_tags_gcs(repo: NoteRepository):
    _insert_note(repo, "n1")
    repo.sync_note_tags("n1", "ws", "u1", [("work/projects", "frontmatter")])
    repo.delete_note_tags("n1", "ws", "u1")
    assert repo.tag_tree("ws", "u1") == []


def test_list_tag_filter_is_prefix_aware(repo: NoteRepository):
    _insert_note(repo, "n1")
    _insert_note(repo, "n2")
    repo.sync_note_tags("n1", "ws", "u1", [("work/projects", "frontmatter")])
    repo.sync_note_tags("n2", "ws", "u1", [("life", "frontmatter")])
    got = {r["note_id"] for r in repo.list("ws", "u1", tags=["work"], limit=None)}
    assert got == {"n1"}  # matched via descendant work/projects
