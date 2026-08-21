"""Correctness for GitRepository.file_histories: one shared walk returning up to
``limit`` newest history entries per path must equal N independent
file_history(p, limit) calls. head_shas_for_paths and file_history are thin
wrappers over it, so this file pins the generalized per-path-countdown walk
directly — interleaved histories, per-path limits, and entry shape."""


def test_limit_two_returns_each_paths_two_newest_without_cross_mixing(git_ws, tmp_path):
    # Interleave commits to a.md and b.md so a walk that mis-assigns matches or
    # shares a single global budget would visibly leak entries across paths.
    for i in range(3):
        (tmp_path / "a.md").write_text(f"a v{i + 1}")
        git_ws.commit_file("a.md", f"note: a v{i + 1}")
        (tmp_path / "b.md").write_text(f"b v{i + 1}")
        git_ws.commit_file("b.md", f"note: b v{i + 1}")

    result = git_ws.file_histories(["a.md", "b.md"], limit=2)

    assert set(result) == {"a.md", "b.md"}
    assert [e["message"] for e in result["a.md"]] == ["note: a v3", "note: a v2"]
    assert [e["message"] for e in result["b.md"]] == ["note: b v3", "note: b v2"]
    for entries in result.values():
        for entry in entries:
            assert set(entry) == {"sha", "message", "timestamp"}


def test_path_shallower_than_limit_gets_its_full_history(git_ws, tmp_path):
    (tmp_path / "deep.md").write_text("deep v1")
    git_ws.commit_file("deep.md", "note: deep v1")
    (tmp_path / "deep.md").write_text("deep v2")
    git_ws.commit_file("deep.md", "note: deep v2")
    (tmp_path / "deep.md").write_text("deep v3")
    git_ws.commit_file("deep.md", "note: deep v3")
    (tmp_path / "shallow.md").write_text("shallow v1")
    git_ws.commit_file("shallow.md", "note: shallow v1")

    result = git_ws.file_histories(["deep.md", "shallow.md", "ghost.md"], limit=2)

    assert [e["message"] for e in result["deep.md"]] == ["note: deep v3", "note: deep v2"]
    assert [e["message"] for e in result["shallow.md"]] == ["note: shallow v1"]
    assert result["ghost.md"] == []


def test_parity_with_independent_file_history_calls(git_ws, tmp_path):
    for i in range(4):
        (tmp_path / "a.md").write_text(f"a v{i + 1}")
        git_ws.commit_file("a.md", f"note: a v{i + 1}")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.md").write_text("c v1")
    git_ws.commit_file("sub/c.md", "note: add c")

    paths = ["a.md", "sub/c.md"]
    assert git_ws.file_histories(paths, limit=3) == {
        p: git_ws.file_history(p, limit=3) for p in paths
    }


def test_empty_repo_and_empty_input_degenerate_cases(git_ws):
    assert git_ws.file_histories(["a.md"], limit=2) == {"a.md": []}
    assert git_ws.file_histories([], limit=2) == {}
