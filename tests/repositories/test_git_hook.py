import time

import pytest

from kajet_turbo import perf
from kajet_turbo.repositories import git as gitmod
from kajet_turbo.repositories.git import (
    GitRepository,
    defer_workspace_postprocess,
    register_post_commit_hook,
)


def test_commit_fires_post_commit_hook(tmp_path, monkeypatch):
    calls: list[str] = []
    # isolate the global hook list for this test
    monkeypatch.setattr(gitmod, "_post_commit_hooks", [])
    register_post_commit_hook(calls.append)

    ws = tmp_path / "ws"
    GitRepository.init(str(ws))
    (ws / "n.md").write_text("x")
    GitRepository(str(ws)).commit_file("n.md", "note: add")

    assert calls == [str(ws)]


def test_hook_exception_does_not_break_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(gitmod, "_post_commit_hooks", [])
    register_post_commit_hook(lambda _p: (_ for _ in ()).throw(RuntimeError("boom")))

    ws = tmp_path / "ws"
    GitRepository.init(str(ws))
    (ws / "n.md").write_text("x")
    # must not raise despite the failing hook
    GitRepository(str(ws)).commit_file("n.md", "note: add")


def test_transaction_coalesces_hooks_and_fires_after_release(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(gitmod, "_post_commit_hooks", [])
    register_post_commit_hook(calls.append)

    ws = tmp_path / "ws"
    repo = GitRepository.init(str(ws))
    with repo.transaction():
        (ws / "a.md").write_text("a")
        repo.commit_file("a.md", "note: add a")
        (ws / "b.md").write_text("b")
        repo.commit_file("b.md", "note: add b")
        assert calls == []

    assert calls == [str(ws)]


def test_transaction_fires_hook_before_deferred_failure(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(gitmod, "_post_commit_hooks", [])
    register_post_commit_hook(calls.append)

    ws = tmp_path / "ws"
    repo = GitRepository.init(str(ws))

    def fail_indexing() -> None:
        raise RuntimeError("index failed")

    with pytest.raises(RuntimeError, match="index failed"), repo.transaction():
        (ws / "n.md").write_text("x")
        repo.commit_file("n.md", "note: add")
        defer_workspace_postprocess(str(ws), fail_indexing)
        assert calls == []

    assert calls == [str(ws)]


def test_nested_transaction_runs_deferred_callbacks_fifo_after_release(tmp_path):
    calls: list[str] = []
    ws = tmp_path / "ws"
    repo = GitRepository.init(str(ws))

    with repo.transaction():
        defer_workspace_postprocess(str(ws), lambda: calls.append("outer"))
        with repo.transaction():
            defer_workspace_postprocess(str(ws), lambda: calls.append("nested"))
        assert calls == []

    assert calls == ["outer", "nested"]


def test_transaction_records_lock_duration_separately_from_git(tmp_path):
    ws = tmp_path / "ws"
    repo = GitRepository.init(str(ws))

    with perf.perf_span() as span, repo.transaction():
        time.sleep(0.05)
        (ws / "n.md").write_text("x")
        repo.commit_file("n.md", "note: add")

    assert span is not None
    assert span.fields["workspace_write_ms"] >= span.fields["git_ms"] + 30
