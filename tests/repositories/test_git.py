from pathlib import Path

import pytest
from dulwich.objects import Commit, Tree
from dulwich.repo import Repo as DulwichRepo

from kajet_turbo.repositories.git import GitError, GitRepository


def test_init_creates_directory_and_git_repo(tmp_path):
    ws = str(tmp_path / "new-ws")
    repo = GitRepository.init(ws)
    assert Path(ws).is_dir()
    assert (Path(ws) / ".git").is_dir()
    assert isinstance(repo, GitRepository)


def test_open_invalid_path_raises_git_error(tmp_path):
    with pytest.raises(GitError):
        GitRepository(str(tmp_path / "nie-istnieje"))


def test_open_non_git_dir_raises_git_error(tmp_path):
    (tmp_path / "zwykly").mkdir()
    with pytest.raises(GitError):
        GitRepository(str(tmp_path / "zwykly"))


def test_commit_file_creates_commit(git_ws, tmp_path):
    (tmp_path / "note.md").write_text("# Test")
    git_ws.commit_file("note.md", "note: add note")

    r = DulwichRepo(str(tmp_path))
    commits = list(r.get_walker())
    assert len(commits) == 1
    assert b"note: add note" in commits[0].commit.message


def test_commit_file_raises_git_error_on_missing_file(git_ws):
    with pytest.raises(GitError):
        git_ws.commit_file("nie-ma-takiego.md", "note: fail")


def test_commit_changes_noop_on_empty(git_ws, tmp_path):
    (tmp_path / "seed.md").write_text("# seed")
    git_ws.commit_file("seed.md", "note: seed")

    git_ws.commit_changes(removed=[], added=[], message="note: nothing")

    assert (tmp_path / "seed.md").exists()
    assert len(list(DulwichRepo(str(tmp_path)).get_walker())) == 1


def test_commit_changes_removal_is_index_only(git_ws, tmp_path):
    """Removal never touches the working tree — the caller owns it."""
    filepath = tmp_path / "note.md"
    filepath.write_text("# Test")
    git_ws.commit_file("note.md", "note: add")

    git_ws.commit_changes(removed=["note.md"], added=[], message="note: delete")

    assert filepath.exists()  # still on disk — commit_changes never unlinks
    r = DulwichRepo(str(tmp_path))
    commits = list(r.get_walker())
    assert len(commits) == 2
    assert b"note: delete" in commits[0].commit.message
    head_commit = r[r.head()]
    assert isinstance(head_commit, Commit)
    tree = r[head_commit.tree]
    assert isinstance(tree, Tree)
    assert b"note.md" not in tree  # gone from HEAD's tree


def test_commit_changes_removes_all_in_single_commit(git_ws, tmp_path):
    (tmp_path / "a.md").write_text("# A")
    (tmp_path / "b.md").write_text("# B")
    (tmp_path / "c.md").write_text("# C")
    git_ws.commit_files(["a.md", "b.md", "c.md"], "note: add 3 notes")

    for name in ("a.md", "b.md"):
        (tmp_path / name).unlink()
    git_ws.commit_changes(removed=["a.md", "b.md"], added=[], message="note: delete 2 notes")

    assert not (tmp_path / "a.md").exists()
    assert not (tmp_path / "b.md").exists()
    assert (tmp_path / "c.md").exists()
    commits = list(DulwichRepo(str(tmp_path)).get_walker())
    assert len(commits) == 2  # add + one batch-delete commit, not split per file
    assert b"note: delete 2 notes" in commits[0].commit.message


def test_commit_changes_raises_on_missing_added_path(git_ws):
    with pytest.raises(GitError):
        git_ws.commit_changes(removed=[], added=["nie-ma-takiego.md"], message="note: fail")


def test_commit_changes_rename_moves_file_and_commits(git_ws, tmp_path):
    old = tmp_path / "stara.md"
    old.write_text("content")
    git_ws.commit_file("stara.md", "note: add")

    old.rename(tmp_path / "nowa.md")
    git_ws.commit_changes(removed=["stara.md"], added=["nowa.md"], message="note: rename")

    assert not old.exists()
    assert (tmp_path / "nowa.md").exists()
    assert (tmp_path / "nowa.md").read_text() == "content"
    r = DulwichRepo(str(tmp_path))
    commits = list(r.get_walker())
    assert len(commits) == 2
    assert b"note: rename" in commits[0].commit.message


def test_commit_file_is_thin_wrapper_over_commit_changes(git_ws, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        type(git_ws),
        "commit_changes",
        lambda self, *, removed, added, message: calls.append((removed, added, message)),
    )
    git_ws.commit_file("note.md", "note: add note")
    assert calls == [([], ["note.md"], "note: add note")]


def test_commit_files_is_thin_wrapper_over_commit_changes(git_ws, monkeypatch):
    calls = []
    monkeypatch.setattr(
        type(git_ws),
        "commit_changes",
        lambda self, *, removed, added, message: calls.append((removed, added, message)),
    )
    git_ws.commit_files(["a.md", "b.md"], "note: add 2 notes")
    assert calls == [([], ["a.md", "b.md"], "note: add 2 notes")]


def test_commit_moves_is_thin_wrapper_over_commit_changes(git_ws, monkeypatch):
    calls = []
    monkeypatch.setattr(
        type(git_ws),
        "commit_changes",
        lambda self, *, removed, added, message: calls.append((removed, added, message)),
    )
    git_ws.commit_moves(["old.md"], ["new.md"], "folder: move x -> y")
    assert calls == [(["old.md"], ["new.md"], "folder: move x -> y")]


def test_file_history_returns_commits_for_file(git_ws, tmp_path):
    (tmp_path / "note.md").write_text("# v1")
    git_ws.commit_file("note.md", "note: add note")
    (tmp_path / "note.md").write_text("# v2")
    git_ws.commit_file("note.md", "note: update note")

    history = git_ws.file_history("note.md")

    assert len(history) == 2
    assert history[0]["message"] == "note: update note"
    assert history[1]["message"] == "note: add note"
    assert all("sha" in h and "timestamp" in h for h in history)


def test_file_history_respects_limit(git_ws, tmp_path):
    for i in range(3):
        (tmp_path / "note.md").write_text(f"# v{i}")
        git_ws.commit_file("note.md", f"note: v{i}")

    history = git_ws.file_history("note.md", limit=2)

    assert len(history) == 2


def test_file_history_returns_empty_for_no_commits(git_ws):
    history = git_ws.file_history("nie-ma-takiego.md")
    assert history == []


def test_file_content_at_commit_returns_historical_content(git_ws, tmp_path):
    (tmp_path / "note.md").write_text("# v1 content")
    git_ws.commit_file("note.md", "note: add note")
    sha_v1 = git_ws.file_history("note.md")[0]["sha"]
    (tmp_path / "note.md").write_text("# v2 content")
    git_ws.commit_file("note.md", "note: update note")

    content = git_ws.file_content_at_commit("note.md", sha_v1)

    assert "# v1 content" in content


def test_file_content_at_commit_raises_on_invalid_sha(git_ws, tmp_path):
    (tmp_path / "note.md").write_text("content")
    git_ws.commit_file("note.md", "note: add")

    with pytest.raises(GitError):
        git_ws.file_content_at_commit("note.md", "0" * 40)


def test_parallel_commits_to_same_repo_do_not_corrupt(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from dulwich.repo import Repo

    from kajet_turbo.repositories.git import GitRepository

    GitRepository.init(str(tmp_path))

    def write_and_commit(i: int) -> None:
        (tmp_path / f"note-{i}.md").write_text(f"content {i}")
        GitRepository(str(tmp_path)).commit_file(f"note-{i}.md", f"add {i}")

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(write_and_commit, range(16)))

    commits = list(Repo(str(tmp_path)).get_walker())
    assert len(commits) == 16


def _commit_one(args: tuple[str, int]) -> None:
    # Module-level so it is picklable under the spawn start method (macOS default).
    ws, i = args
    from kajet_turbo.repositories.git import GitRepository

    Path(ws, f"proc-{i}.md").write_text(f"content {i}")
    GitRepository(ws).commit_file(f"proc-{i}.md", f"add {i}")


def _append_in_transaction(args: tuple[str, int]) -> None:
    """Module-level so multiprocessing can pickle it under spawn."""
    ws, i = args
    repo = GitRepository(ws)
    path = Path(ws, "shared.txt")
    with repo.transaction():
        current = path.read_text()
        path.write_text(f"{current}{i}\n")
        repo.commit_file("shared.txt", f"append {i}")


def test_parallel_commits_across_processes_keep_all(tmp_path):
    """Cross-process: the in-process threading.Lock does not span processes, so
    without the flock two processes racing add+commit overwrite HEAD and lose a
    commit. This exercises the cross-process lock that the thread test cannot."""
    import multiprocessing as mp

    from dulwich.repo import Repo

    from kajet_turbo.repositories.git import GitRepository

    GitRepository.init(str(tmp_path))

    ctx = mp.get_context("spawn")
    with ctx.Pool(8) as pool:
        pool.map(_commit_one, [(str(tmp_path), i) for i in range(8)])

    commits = list(Repo(str(tmp_path)).get_walker())
    assert len(commits) == 8


def test_transaction_serializes_read_modify_write_across_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    repo = GitRepository.init(str(tmp_path))
    path = tmp_path / "shared.txt"
    path.write_text("base\n")
    repo.commit_file("shared.txt", "seed")
    first_acquired = Event()
    release_first = Event()
    second_acquired = Event()

    def first() -> None:
        with GitRepository(str(tmp_path)).transaction() as tx:
            current = path.read_text()
            first_acquired.set()
            assert release_first.wait(timeout=2)
            path.write_text(f"{current}first\n")
            tx.commit_file("shared.txt", "first")

    def second() -> None:
        with GitRepository(str(tmp_path)).transaction() as tx:
            second_acquired.set()
            current = path.read_text()
            path.write_text(f"{current}second\n")
            tx.commit_file("shared.txt", "second")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        assert first_acquired.wait(timeout=2)
        second_future = pool.submit(second)
        assert not second_acquired.wait(timeout=0.1)
        release_first.set()
        first_future.result(timeout=2)
        second_future.result(timeout=2)

    assert path.read_text() == "base\nfirst\nsecond\n"


def test_transaction_serializes_read_modify_write_across_processes(tmp_path):
    import multiprocessing as mp

    repo = GitRepository.init(str(tmp_path))
    path = tmp_path / "shared.txt"
    path.write_text("")
    repo.commit_file("shared.txt", "seed")

    ctx = mp.get_context("spawn")
    with ctx.Pool(4) as pool:
        pool.map(_append_in_transaction, [(str(tmp_path), i) for i in range(4)])

    assert set(path.read_text().splitlines()) == {"0", "1", "2", "3"}
    assert len(list(DulwichRepo(str(tmp_path)).get_walker())) == 5


def test_transaction_releases_lock_after_exception(tmp_path):
    repo = GitRepository.init(str(tmp_path))

    with pytest.raises(RuntimeError, match="boom"), repo.transaction():
        raise RuntimeError("boom")

    with repo.transaction():
        (tmp_path / "after.md").write_text("ok")
        repo.commit_file("after.md", "after")

    assert (tmp_path / "after.md").read_text() == "ok"


def test_commit_files_creates_single_commit_over_many(git_ws, tmp_path):
    (tmp_path / "a.md").write_text("# A")
    (tmp_path / "b.md").write_text("# B")
    (tmp_path / "c.md").write_text("# C")

    git_ws.commit_files(["a.md", "b.md", "c.md"], "note: add 3 notes")

    dulwich_repo = DulwichRepo(str(tmp_path))
    commits = list(dulwich_repo.get_walker())
    assert len(commits) == 1
    assert b"note: add 3 notes" in commits[0].commit.message
    tree = dulwich_repo[commits[0].commit.tree]
    tree_names = {item.path for item in tree.items()}  # ty: ignore[unresolved-attribute] - dulwich Tree
    assert b"a.md" in tree_names
    assert b"b.md" in tree_names
    assert b"c.md" in tree_names


def test_commit_files_empty_is_noop(git_ws, tmp_path):
    (tmp_path / "seed.md").write_text("# seed")
    git_ws.commit_file("seed.md", "note: seed")

    git_ws.commit_files([], "note: nothing")

    assert len(list(DulwichRepo(str(tmp_path)).get_walker())) == 1


def test_commit_files_raises_on_missing_file(git_ws, tmp_path):
    (tmp_path / "a.md").write_text("# A")
    with pytest.raises(GitError):
        git_ws.commit_files(["a.md", "missing.md"], "note: add")


def test_init_defaults_to_main_branch(tmp_path):
    from kajet_turbo.repositories.git import GitRepository
    from kajet_turbo.repositories.git_push import current_branch

    ws = tmp_path / "ws"
    GitRepository.init(str(ws))
    (ws / "n.md").write_text("x")
    GitRepository(str(ws)).commit_file("n.md", "note: add")
    assert current_branch(str(ws)) == b"refs/heads/main"


def test_rename_master_to_main(tmp_path):
    from dulwich import porcelain
    from dulwich.repo import Repo

    from kajet_turbo.repositories.git import GitRepository
    from kajet_turbo.repositories.git_push import current_branch

    ws = tmp_path / "ws"
    porcelain.init(str(ws))  # legacy: starts on master
    (ws / "n.md").write_text("x")
    porcelain.add(str(ws), paths=["n.md"])
    porcelain.commit(str(ws), message=b"c", author=b"t <t@t>", committer=b"t <t@t>")
    assert current_branch(str(ws)) == b"refs/heads/master"

    assert GitRepository(str(ws)).rename_master_to_main() is True
    assert current_branch(str(ws)) == b"refs/heads/main"
    refs = Repo(str(ws)).refs
    assert b"refs/heads/main" in refs and b"refs/heads/master" not in refs  # ty: ignore[unsupported-operator] - dulwich RefsContainer supports __contains__


def test_rename_master_to_main_idempotent(tmp_path):
    from kajet_turbo.repositories.git import GitRepository

    ws = tmp_path / "ws"
    GitRepository.init(str(ws))  # already main (Task 7)
    (ws / "n.md").write_text("x")
    GitRepository(str(ws)).commit_file("n.md", "c")
    assert GitRepository(str(ws)).rename_master_to_main() is False
