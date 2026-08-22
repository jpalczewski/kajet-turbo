"""save_many() batch coverage for NoteService."""

from unittest.mock import patch

import pytest


def _commit_count(workspace):
    from dulwich.repo import Repo as DulwichRepo

    try:
        return len(list(DulwichRepo(str(workspace)).get_walker()))
    except KeyError:
        return 0  # empty repo: no HEAD yet


def test_save_many_happy_path_single_commit(service, workspace):
    before = _commit_count(workspace)
    notes = [
        {"title": "Batch A", "content": "alpha"},
        {"title": "Batch B", "content": "beta", "tags": ["x"]},
        {"title": "Batch C", "content": "gamma", "folder": "docs"},
    ]
    results = service.save_many("u1", "ws", str(workspace), notes)

    assert [r["index"] for r in results] == [0, 1, 2]
    assert all("note_id" in r for r in results)
    assert (workspace / "Batch A.md").exists()
    assert (workspace / "docs" / "Batch C.md").exists()
    # Exactly one new commit for the whole batch.
    assert _commit_count(workspace) == before + 1


def test_save_many_best_effort_reports_per_note(service, workspace):
    service.save("u1", "ws", str(workspace), "Existing", "x", [])
    notes = [
        {"title": "Fresh One", "content": "a"},
        {"title": "Existing", "content": "dup"},  # collides with DB
        {"title": "", "content": "no title"},  # empty title
        {"title": "Fresh Two", "content": "b"},
    ]
    results = service.save_many("u1", "ws", str(workspace), notes)

    assert "note_id" in results[0]
    assert "error" in results[1]
    assert "error" in results[2]
    assert "note_id" in results[3]


def test_save_many_intra_batch_duplicate(service, workspace):
    notes = [
        {"title": "Same", "content": "first"},
        {"title": "Same", "content": "second"},  # same (folder, title) within batch
    ]
    results = service.save_many("u1", "ws", str(workspace), notes)

    assert "note_id" in results[0]
    assert "error" in results[1]
    assert "batchu" in results[1]["error"].lower()


def test_save_many_empty_list(service, workspace):
    assert service.save_many("u1", "ws", str(workspace), []) == []


def test_save_many_cross_batch_wikilink_order_independent(service, workspace):
    # Note A links to B; B comes AFTER A in input order — must still resolve.
    notes = [
        {"title": "A note", "content": "links to [[B note]]"},
        {"title": "B note", "content": "target"},
    ]
    results = service.save_many("u1", "ws", str(workspace), notes)

    assert "note_id" in results[0]
    assert "note_id" in results[1]
    b_id = results[1]["note_id"]
    # A's link edge resolves to B's note_id.
    assert service._link_service._link_repo.outlinks(results[0]["note_id"]) == [b_id]


def test_save_many_non_cascading_drop(service, workspace):
    # A links to B (valid in-batch); B has its own broken link and is dropped.
    notes = [
        {"title": "A links B", "content": "see [[B broken]]"},
        {"title": "B broken", "content": "see [[Does Not Exist]]"},
    ]
    results = service.save_many("u1", "ws", str(workspace), notes)

    assert "note_id" in results[0]  # A still saved
    assert "error" in results[1]  # B dropped for its own broken link


def test_save_many_git_error_rolls_back_all_files(service, workspace):
    from kajet_turbo.repositories.git import GitError

    notes = [{"title": "RB One", "content": "a"}, {"title": "RB Two", "content": "b"}]
    with (
        patch(
            "kajet_turbo.repositories.git.GitRepository.commit_files",
            side_effect=GitError("fail"),
        ),
        pytest.raises(GitError),
    ):
        service.save_many("u1", "ws", str(workspace), notes)

    md_files = [p for p in workspace.rglob("*.md") if ".git" not in str(p)]
    assert md_files == []


def test_save_many_indexes_every_valid_note(service, workspace):
    notes = [{"title": "Idx A", "content": "a"}, {"title": "Idx B", "content": "b"}]
    with patch.object(service._indexer, "index_many") as idx:
        service.save_many("u1", "ws", str(workspace), notes)
    idx.assert_called_once()
    passed = idx.call_args.args[2]
    assert {n["title"] for n in passed} == {"Idx A", "Idx B"}


def test_save_many_filename_collision_dedup(service, workspace):
    # "A:B" and "A B" both sanitize to "A B.md" (colon is Windows-forbidden).
    notes = [
        {"title": "A:B", "content": "first"},
        {"title": "A B", "content": "second"},
    ]
    results = service.save_many("u1", "ws", str(workspace), notes)

    assert "note_id" in results[0]
    assert "error" in results[1]
    assert "kolizja" in results[1]["error"].lower()

    # Only one .md file with that name should exist.
    md_files = [p for p in workspace.rglob("A B.md") if ".git" not in str(p)]
    assert len(md_files) == 1

    # The surviving file's content must belong to the FIRST note.
    from kajet_turbo.workspace import read_note_file

    data = read_note_file(str(md_files[0]))
    assert data["content"].strip() == "first"

    # DB row for the first note exists; no second row for "A B".
    note = service._crud_repo.get(results[0]["note_id"], owner_id="u1")
    assert note is not None
    assert note.title == "A:B"
