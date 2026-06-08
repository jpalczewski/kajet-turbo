# tests/test_git_ops.py
import subprocess
import pytest
from pathlib import Path
from kajet_turbo.git_ops import commit_file, delete_file_commit, GitError


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), check=True, capture_output=True,
    )
    return tmp_path


def test_commit_file_creates_commit(git_repo):
    notes_dir = git_repo / "notes"
    notes_dir.mkdir()
    filepath = notes_dir / "abc1234-test.md"
    filepath.write_text("# Test")

    commit_file(str(git_repo), "notes/abc1234-test.md", "note: add Test")

    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=str(git_repo), capture_output=True, text=True, check=True,
    )
    assert "note: add Test" in log.stdout


def test_delete_file_commit_removes_file(git_repo):
    notes_dir = git_repo / "notes"
    notes_dir.mkdir()
    filepath = notes_dir / "abc1234-test.md"
    filepath.write_text("# Test")
    commit_file(str(git_repo), "notes/abc1234-test.md", "note: add Test")

    delete_file_commit(str(git_repo), "notes/abc1234-test.md", "note: delete Test")

    assert not filepath.exists()
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=str(git_repo), capture_output=True, text=True, check=True,
    )
    assert "note: delete Test" in log.stdout


def test_commit_raises_on_missing_file(git_repo):
    with pytest.raises(GitError):
        commit_file(str(git_repo), "notes/nieistnieje.md", "note: fail")
