# src/kajet_turbo/git_ops.py
from git import Repo, InvalidGitRepositoryError, GitCommandError


class GitError(Exception):
    pass


def commit_file(workspace_path: str, relative_path: str, message: str) -> None:
    try:
        repo = Repo(workspace_path)
        repo.index.add([relative_path])
        repo.index.commit(message)
    except (InvalidGitRepositoryError, GitCommandError, FileNotFoundError) as e:
        raise GitError(str(e)) from e


def delete_file_commit(workspace_path: str, relative_path: str, message: str) -> None:
    try:
        repo = Repo(workspace_path)
        repo.index.remove([relative_path], working_tree=True)
        repo.index.commit(message)
    except (InvalidGitRepositoryError, GitCommandError) as e:
        raise GitError(str(e)) from e
