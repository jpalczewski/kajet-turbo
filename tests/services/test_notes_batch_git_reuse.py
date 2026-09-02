"""Regression guard: batch note operations open the workspace git repo ONCE.

get_many/edit_many/delete_many used to construct a fresh GitRepository (and thus
a fresh dulwich Repo, re-opening refs and pack indexes) per note in the batch —
an N+1 that produced multi-second batch reads in production. These tests count
Repo constructions in kajet_turbo.repositories.git and pin them to one per batch
regardless of batch size. porcelain's internal repo opens (commit paths) use
dulwich's own import and are deliberately not counted.

They also pin a second regression: head sha resolution itself must be ONE shared
git_walker pass over the batch (head_shas_for_paths), not N per-note walks
(file_history) — that's the actual fix for the multi-second batch reads, one repo
open alone would not have been enough.
"""

import pytest
from dulwich.repo import BaseRepo

import kajet_turbo.repositories.git as git_module
from kajet_turbo.repositories.git import GitRepository


@pytest.fixture
def repo_open_count(monkeypatch):
    real_repo = git_module.Repo
    calls = {"count": 0}

    def counting_repo(*args, **kwargs):
        calls["count"] += 1
        return real_repo(*args, **kwargs)

    monkeypatch.setattr(git_module, "Repo", counting_repo)
    return calls


@pytest.fixture
def walker_pass_count(monkeypatch):
    """Counts dulwich walker construction (BaseRepo.get_walker) — the unit of
    "one git history pass". Patched on BaseRepo, not Repo, since get_walker is
    defined there and reached the same way regardless of the concrete repo class."""
    real = BaseRepo.get_walker
    calls = {"count": 0}

    def counting(self, *args, **kwargs):
        calls["count"] += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(BaseRepo, "get_walker", counting)
    return calls


def _saved_notes(service, workspace, count=3):
    notes = []
    for i in range(count):
        title = f"Note {i}"
        result = service.save("u1", "ws", str(workspace), title, f"body {i}\n", [])
        sha = GitRepository(str(workspace)).file_history(f"{title}.md", limit=1)[0]["sha"]
        notes.append({"note_id": result["note_id"], "sha": sha})
    return notes


def test_get_many_opens_repo_once(service, workspace, repo_open_count):
    notes = _saved_notes(service, workspace)
    repo_open_count["count"] = 0

    results = service.get_many([n["note_id"] for n in notes], "u1", str(workspace))

    assert len(results) == len(notes)
    assert repo_open_count["count"] == 1


def test_edit_many_opens_repo_once(service, workspace, repo_open_count):
    notes = _saved_notes(service, workspace)
    repo_open_count["count"] = 0

    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            {"note_id": n["note_id"], "expected_sha": n["sha"], "mode": "append", "content": "x"}
            for n in notes
        ],
    )

    assert result["applied"] is True
    assert repo_open_count["count"] == 1


def test_delete_many_opens_repo_once(service, workspace, repo_open_count):
    notes = _saved_notes(service, workspace)
    repo_open_count["count"] = 0

    result = service.delete_many(
        "u1",
        "ws",
        str(workspace),
        [{"note_id": n["note_id"], "expected_sha": n["sha"]} for n in notes],
    )

    assert result["applied"] is True
    assert repo_open_count["count"] == 1


def test_get_many_resolves_shas_in_one_walker_pass(service, workspace, walker_pass_count):
    notes = _saved_notes(service, workspace)
    walker_pass_count["count"] = 0

    results = service.get_many([n["note_id"] for n in notes], "u1", str(workspace))

    assert len(results) == len(notes)
    assert walker_pass_count["count"] == 1


def test_edit_many_resolves_shas_in_one_walker_pass(service, workspace, walker_pass_count):
    notes = _saved_notes(service, workspace)
    walker_pass_count["count"] = 0

    result = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            {"note_id": n["note_id"], "expected_sha": n["sha"], "mode": "append", "content": "x"}
            for n in notes
        ],
    )

    assert result["applied"] is True
    assert walker_pass_count["count"] == 1


def test_delete_many_resolves_shas_in_one_walker_pass(service, workspace, walker_pass_count):
    notes = _saved_notes(service, workspace)
    walker_pass_count["count"] = 0

    result = service.delete_many(
        "u1",
        "ws",
        str(workspace),
        [{"note_id": n["note_id"], "expected_sha": n["sha"]} for n in notes],
    )

    assert result["applied"] is True
    assert walker_pass_count["count"] == 1


def test_update_rename_with_backlink_opens_repo_once_for_the_rename_leg(
    service, workspace, repo_open_count
):
    """#123: rewrite_backlinks used to open its own second GitRepository even though
    update()'s rename leg already has one open. The staleness check just above the
    rename leg (``current_head_sha``) is a separate, pre-existing open outside #123's
    scope, so the fixed count is 2 (staleness + the rename/rewrite pair sharing one
    repo), not 1 — the regression this guards is the rename/rewrite pair going from
    two opens to one, taking the call's total from 3 to 2."""
    target = service.save("u1", "ws", str(workspace), "Target", "body\n", [])
    source = service.save("u1", "ws", str(workspace), "Source", "links [[Target]]\n", [])
    sha = GitRepository(str(workspace)).file_history("Target.md", limit=1)[0]["sha"]
    repo_open_count["count"] = 0

    result = service.update(
        target["note_id"], "u1", str(workspace), expected_sha=sha, title="Renamed"
    )

    assert result["note_id"] == target["note_id"]
    assert repo_open_count["count"] == 2
    assert (
        "[[Renamed]]" in service.get_with_content(source["note_id"], "u1", str(workspace)).content
    )


def test_move_opens_repo_once(service, workspace, repo_open_count):
    target = service.save("u1", "ws", str(workspace), "Target", "body\n", [])
    service.save("u1", "ws", str(workspace), "Source", "links [[Target]]\n", [])
    repo_open_count["count"] = 0

    result = service.move(target["note_id"], "u1", str(workspace), "moved")

    assert result["folder"] == "moved"
    assert repo_open_count["count"] == 1


def test_move_folder_opens_repo_once(service, workspace, repo_open_count):
    service.save("u1", "ws", str(workspace), "A", "body\n", [], folder="src")
    service.save("u1", "ws", str(workspace), "B", "links [[A]]\n", [], folder="src")
    repo_open_count["count"] = 0

    result = service.move_folder(
        "src", "dst", owner_id="u1", ws_path=str(workspace), workspace="ws"
    )

    assert result["moved"] == 2
    assert repo_open_count["count"] == 1
