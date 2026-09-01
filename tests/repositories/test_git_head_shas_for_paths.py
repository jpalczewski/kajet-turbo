"""Correctness for GitRepository.head_shas_for_paths: one shared walk must return
exactly what N independent file_history(p, limit=1) calls would return — the whole
point is a performance change with zero semantic difference. Every test here proves
parity against file_history rather than asserting a hardcoded sha, so a regression
in matching logic (not just a wrong answer) gets caught.
"""

from pathlib import Path
from unittest import mock

from dulwich.walk import WalkEntry

from kajet_turbo.repositories.git import GitRepository


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
    git_ws.commit_changes(removed=["a.md"], added=[], message="note: delete a")

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


def test_duplicate_path_in_input_collapses_to_one_key(git_ws, tmp_path):
    (tmp_path / "a.md").write_text("a v1")
    git_ws.commit_file("a.md", "note: add a")
    (tmp_path / "a.md").write_text("a v2")
    git_ws.commit_file("a.md", "note: update a")

    # The dict-keyed result collapses duplicates; one shared walk still resolves them.
    expected = _expected(git_ws, ["a.md"])
    assert git_ws.head_shas_for_paths(["a.md", "a.md"]) == expected


def test_parity_for_path_deleted_and_recreated_returns_newest(git_ws, tmp_path):
    # Two distinct files have lived at a.md over history: an original that was
    # deleted, then a fresh one created under the same name. The head sha must be
    # the NEWEST commit touching a.md, not the oldest — parity against
    # file_history(limit=1) pins that the walk stops at the most recent match.
    (tmp_path / "a.md").write_text("a original")
    git_ws.commit_file("a.md", "note: add a")
    Path(tmp_path / "a.md").unlink()
    git_ws.commit_changes(removed=["a.md"], added=[], message="note: delete a")
    (tmp_path / "a.md").write_text("a recreated")
    git_ws.commit_file("a.md", "note: recreate a")
    for i in range(3):
        (tmp_path / "a.md").write_text(f"a recreated v{i + 2}")
        git_ws.commit_file("a.md", f"note: update a {i}")

    paths = ["a.md"]
    expected = _expected(git_ws, paths)
    result = git_ws.head_shas_for_paths(paths)

    assert result == expected
    # Explicitly assert "newest, not oldest": it must equal the most recent commit.
    assert result["a.md"] == git_ws.file_history("a.md", limit=1)[0]["sha"]


def test_parity_prefix_named_siblings_do_not_cross_contaminate(git_ws, tmp_path):
    """A path that is a string-prefix of another must not steal its match. This is
    the reachable slice of the file<->directory-prefix concern: two notes whose
    relatives share a leading run of bytes ("a.md" vs "ab.md") or where one sits in
    a folder whose name starts like another note ("a.md" vs "a/b.md"). _matching_followed
    replicates dulwich's directory-boundary rule (prefix match only at a "/" edge),
    so none of these collide. A naive startswith without the boundary byte would
    mis-assign "ab.md"'s history to "a.md" and this parity check would catch it.
    """
    (tmp_path / "a.md").write_text("a v1")
    git_ws.commit_file("a.md", "note: add a")
    (tmp_path / "ab.md").write_text("ab v1")
    git_ws.commit_file("ab.md", "note: add ab")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b.md").write_text("a/b v1")
    git_ws.commit_file("a/b.md", "note: add a/b")

    # Bury each path at a different depth so a cross-match would surface as a wrong sha.
    (tmp_path / "ab.md").write_text("ab v2")
    git_ws.commit_file("ab.md", "note: update ab")
    for i in range(3):
        (tmp_path / "a.md").write_text(f"a v{i + 2}")
        git_ws.commit_file("a.md", f"note: update a {i}")

    paths = ["a.md", "ab.md", "a/b.md"]
    expected = _expected(git_ws, paths)

    assert git_ws.head_shas_for_paths(paths) == expected
    # All three resolve to distinct commits — proves no accidental sharing.
    assert len(set(expected.values())) == 3


def test_parity_when_one_change_matches_multiple_followed_paths(git_ws, tmp_path):
    """One TreeChange can match SEVERAL followed paths at once: after file a.md is
    deleted and a.md/b.md is committed (a.md now a directory), the add-change's
    path b"a.md/b.md" is an exact match for followed "a.md/b.md" AND a
    directory-prefix match for followed "a.md" — dulwich's per-path walk yields
    that commit for BOTH, so the shared walk must credit both from the single
    change. A matcher that pops only the first hit resolves "a.md" one commit too
    deep (at its delete commit). Found by adversarial review; unreachable through
    the notes service today, but the walk must be correct on its own."""
    (tmp_path / "a.md").write_text("a as a file")
    git_ws.commit_file("a.md", "note: add a")
    Path(tmp_path / "a.md").unlink()
    git_ws.commit_changes(removed=["a.md"], added=[], message="note: delete a")
    (tmp_path / "a.md").mkdir()
    (tmp_path / "a.md" / "b.md").write_text("b inside a directory named a.md")
    git_ws.commit_file("a.md/b.md", "note: add b under dir a.md")

    paths = ["a.md", "a.md/b.md"]
    expected = _expected(git_ws, paths)
    result = git_ws.head_shas_for_paths(paths)

    assert result == expected
    # Both resolve to the SAME commit — the one that added a.md/b.md — because a
    # change under a directory also counts as touching the directory path.
    assert result["a.md"] == result["a.md/b.md"]
    assert result["a.md"] == git_ws.file_history("a.md/b.md", limit=1)[0]["sha"]


def test_early_exit_stops_walk_once_all_paths_resolved(git_ws, tmp_path):
    """The entire performance point of head_shas_for_paths is that the shared walk
    stops as soon as every requested path is resolved, instead of touring the rest
    of history — mutation testing on this fix found that removing the `break` passes
    every OTHER test unnoticed, because correctness doesn't depend on it, only
    performance does. Proven here by counting how many commits the walk actually
    visits (WalkEntry.changes is called exactly once per commit the walker inspects,
    matching or not — see git.py's _flat_changes) rather than by wall-clock timing,
    which would be flaky.

    Two targets are buried at shallow depth near HEAD; a long run of unrelated
    filler commits sits behind them. A working early exit never looks past the
    targets. Without it, the walk would inspect every filler commit too.
    """
    FILLER_COUNT = 60
    (tmp_path / "filler.md").write_text("filler v0")
    git_ws.commit_file("filler.md", "note: add filler")
    for i in range(FILLER_COUNT):
        (tmp_path / "filler.md").write_text(f"filler v{i + 1}")
        git_ws.commit_file("filler.md", f"note: update filler {i}")

    (tmp_path / "b.md").write_text("b v1")
    git_ws.commit_file("b.md", "note: add b")
    for i in range(8):
        (tmp_path / "filler.md").write_text(f"filler v{FILLER_COUNT + i + 1}")
        git_ws.commit_file("filler.md", f"note: update filler {FILLER_COUNT + i}")
    (tmp_path / "a.md").write_text("a v1")
    git_ws.commit_file("a.md", "note: add a")

    paths = ["a.md", "b.md"]
    expected = _expected(git_ws, paths)

    visited: list[object] = []
    real_changes = WalkEntry.changes

    def counting_changes(self, *args, **kwargs):
        visited.append(self)
        return real_changes(self, *args, **kwargs)

    with mock.patch.object(WalkEntry, "changes", counting_changes):
        result = git_ws.head_shas_for_paths(paths)

    assert result == expected
    # a.md (HEAD) then 8 filler + b.md ≈ 10 commits to resolve both. A regression to
    # walking the full history would visit FILLER_COUNT + 10 ≈ 70 — the gap between
    # these two numbers is wide enough that this threshold is not flaky.
    assert len(visited) <= 15, (
        f"walked {len(visited)} commits to resolve 2 shallow paths — "
        "the early exit does not appear to be stopping the walk"
    )
