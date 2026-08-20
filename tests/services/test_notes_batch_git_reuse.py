"""Regression guard: batch note operations open the workspace git repo ONCE.

get_many/edit_many/delete_many used to construct a fresh GitRepository (and thus
a fresh dulwich Repo, re-opening refs and pack indexes) per note in the batch —
an N+1 that produced multi-second batch reads in production. These tests count
Repo constructions in kajet_turbo.repositories.git and pin them to one per batch
regardless of batch size. porcelain's internal repo opens (commit paths) use
dulwich's own import and are deliberately not counted.
"""

import pytest

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
