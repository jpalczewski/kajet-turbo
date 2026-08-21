"""Correctness for GitRepository.head_shas_for_paths: one shared walk must return
exactly what N independent file_history(p, limit=1) calls would return — the whole
point is a performance change with zero semantic difference. Every test here proves
parity against file_history rather than asserting a hardcoded sha, so a regression
in matching logic (not just a wrong answer) gets caught.
"""

from pathlib import Path

import pytest

from kajet_turbo.repositories.git import GitRepository


@pytest.fixture
def git_ws(tmp_path, git_workspace_factory):
    git_workspace_factory(".")
    return GitRepository(str(tmp_path))


def _expected(git_ws: GitRepository, paths: list[str]) -> dict[str, str | None]:
    """The definition of correctness: what N independent file_history(p, limit=1)
    calls would return."""
    return {p: (h[0]["sha"] if (h := git_ws.file_history(p, limit=1)) else None) for p in paths}


def test_parity_across_varied_history_depths(git_ws, tmp_path):
    (tmp_path / "a.md").write_text("a v1")
    git_ws.commit_file("a.md", "note: add a")
    (tmp_path / "b.md").write_text("b v1")
    git_ws.commit_file("b.md", "note: add b")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.md").write_text("c v1")
    git_ws.commit_file("sub/c.md", "note: add c")

    # Push b.md and sub/c.md deep into history with commits that touch only a.md.
    for i in range(5):
        (tmp_path / "a.md").write_text(f"a v{i + 2}")
        git_ws.commit_file("a.md", f"note: update a {i}")

    # Touch b.md once more so its head sha is not its original add-commit.
    (tmp_path / "b.md").write_text("b v2")
    git_ws.commit_file("b.md", "note: update b")

    paths = ["a.md", "b.md", "sub/c.md"]
    expected = _expected(git_ws, paths)

    assert git_ws.head_shas_for_paths(paths) == expected
    assert all(sha is not None for sha in expected.values())


def test_parity_with_path_that_has_no_history(git_ws, tmp_path):
    (tmp_path / "a.md").write_text("a v1")
    git_ws.commit_file("a.md", "note: add a")

    paths = ["a.md", "ghost.md"]
    expected = _expected(git_ws, paths)

    assert expected == {"a.md": expected["a.md"], "ghost.md": None}
    assert git_ws.head_shas_for_paths(paths) == expected


def test_parity_for_deleted_file(git_ws, tmp_path):
    (tmp_path / "a.md").write_text("a v1")
    git_ws.commit_file("a.md", "note: add a")
    Path(tmp_path / "a.md").unlink()
    git_ws.delete_file("a.md", "note: delete a")

    paths = ["a.md"]
    expected = _expected(git_ws, paths)

    assert expected["a.md"] is not None
    assert git_ws.head_shas_for_paths(paths) == expected


def test_empty_repo_returns_all_none_without_raising(git_ws):
    result = git_ws.head_shas_for_paths(["a.md", "b.md"])
    assert result == {"a.md": None, "b.md": None}


def test_empty_path_list_returns_empty_dict(git_ws):
    assert git_ws.head_shas_for_paths([]) == {}


def test_parity_single_path_degenerate_case(git_ws, tmp_path):
    (tmp_path / "a.md").write_text("a v1")
    git_ws.commit_file("a.md", "note: add a")
    (tmp_path / "a.md").write_text("a v2")
    git_ws.commit_file("a.md", "note: update a")

    expected = _expected(git_ws, ["a.md"])
    assert git_ws.head_shas_for_paths(["a.md"]) == expected
