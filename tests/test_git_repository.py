import pytest
from pathlib import Path
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
def git_ws(tmp_path):
    return GitRepository.init(str(tmp_path))


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
