from pathlib import Path

import pytest
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


@pytest.fixture
def git_ws(tmp_path, git_workspace_factory):
    git_workspace_factory(".")
    return GitRepository(str(tmp_path))


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


def test_delete_file_removes_from_disk_and_commits(git_ws, tmp_path):
    filepath = tmp_path / "note.md"
    filepath.write_text("# Test")
    git_ws.commit_file("note.md", "note: add")

    git_ws.delete_file("note.md", "note: delete")

    assert not filepath.exists()
    r = DulwichRepo(str(tmp_path))
    commits = list(r.get_walker())
    assert len(commits) == 2
    assert b"note: delete" in commits[0].commit.message


def test_rename_file_moves_file_and_commits(git_ws, tmp_path):
    old = tmp_path / "stara.md"
    old.write_text("content")
    git_ws.commit_file("stara.md", "note: add")

    git_ws.rename_file("stara.md", "nowa.md", "note: rename")

    assert not old.exists()
    assert (tmp_path / "nowa.md").exists()
    assert (tmp_path / "nowa.md").read_text() == "content"
    r = DulwichRepo(str(tmp_path))
    commits = list(r.get_walker())
    assert len(commits) == 2
    assert b"note: rename" in commits[0].commit.message


def test_rename_file_creates_parent_dirs(git_ws, tmp_path):
    (tmp_path / "note.md").write_text("content")
    git_ws.commit_file("note.md", "note: add")

    git_ws.rename_file("note.md", "Projekty/Klient A/note.md", "note: move")

    assert (tmp_path / "Projekty" / "Klient A" / "note.md").exists()


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
