from kajet_turbo.repositories import git as gitmod
from kajet_turbo.repositories.git import GitRepository, register_post_commit_hook


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
