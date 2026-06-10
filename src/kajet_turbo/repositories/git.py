from pathlib import Path

from dulwich import porcelain
from dulwich.errors import NotGitRepository
from dulwich.repo import Repo

COMMITTER = b"Kajet <bot@kajet.app>"


class GitError(Exception):
    pass


class GitRepository:
    def __init__(self, workspace_path: str) -> None:
        self._workspace_path = workspace_path
        try:
            Repo(workspace_path)
        except (NotGitRepository, Exception) as e:
            raise GitError(str(e)) from e

    @classmethod
    def init(cls, path: str) -> "GitRepository":
        porcelain.init(path)
        return cls(path)

    def commit_file(self, relative_path: str, message: str) -> None:
        try:
            if not Path(self._workspace_path, relative_path).exists():
                raise GitError(f"File not found: {relative_path}")
            porcelain.add(self._workspace_path, paths=[relative_path])
            porcelain.commit(
                self._workspace_path,
                message=message.encode(),
                author=COMMITTER,
                committer=COMMITTER,
            )
        except Exception as e:
            raise GitError(str(e)) from e

    def delete_file(self, relative_path: str, message: str) -> None:
        try:
            Path(self._workspace_path, relative_path).unlink(missing_ok=True)
            porcelain.rm(self._workspace_path, paths=[relative_path])
            porcelain.commit(
                self._workspace_path,
                message=message.encode(),
                author=COMMITTER,
                committer=COMMITTER,
            )
        except GitError:
            raise
        except Exception as e:
            raise GitError(str(e)) from e

    def rename_file(self, old_rel: str, new_rel: str, message: str) -> None:
        try:
            old_full = Path(self._workspace_path, old_rel)
            new_full = Path(self._workspace_path, new_rel)
            new_full.parent.mkdir(parents=True, exist_ok=True)
            old_full.rename(new_full)
            porcelain.rm(self._workspace_path, paths=[old_rel])
            porcelain.add(self._workspace_path, paths=[new_rel])
            porcelain.commit(
                self._workspace_path,
                message=message.encode(),
                author=COMMITTER,
                committer=COMMITTER,
            )
        except GitError:
            raise
        except Exception as e:
            raise GitError(str(e)) from e
