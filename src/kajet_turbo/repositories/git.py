import threading
from pathlib import Path

from dulwich import porcelain
from dulwich.errors import NotGitRepository
from dulwich.object_store import tree_lookup_path
from dulwich.objects import Blob, Commit
from dulwich.repo import Repo

COMMITTER = b"Kajet <bot@kajet.app>"

_REPO_LOCKS: dict[str, threading.Lock] = {}
_REPO_LOCKS_GUARD = threading.Lock()


def _repo_lock(workspace_path: str) -> threading.Lock:
    key = str(Path(workspace_path).resolve())
    with _REPO_LOCKS_GUARD:
        lock = _REPO_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _REPO_LOCKS[key] = lock
        return lock


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
    def init(cls, path: str) -> GitRepository:
        porcelain.init(path)
        return cls(path)

    def commit_file(self, relative_path: str, message: str) -> None:
        with _repo_lock(self._workspace_path):
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
        with _repo_lock(self._workspace_path):
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
        with _repo_lock(self._workspace_path):
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

    def last_commit_time(self) -> int | None:
        try:
            repo = Repo(self._workspace_path)
            walker = repo.get_walker(max_entries=1)
            for entry in walker:
                return entry.commit.author_time
            return None
        except Exception:
            return None

    def file_history(self, relative_path: str, limit: int = 50) -> list[dict]:
        try:
            repo = Repo(self._workspace_path)
            walker = repo.get_walker(paths=[relative_path.encode()], max_entries=limit)
            return [
                {
                    "sha": entry.commit.id.decode("ascii"),
                    "message": entry.commit.message.decode("utf-8", errors="replace").strip(),
                    "timestamp": entry.commit.author_time,
                }
                for entry in walker
            ]
        except KeyError:
            return []
        except Exception as e:
            raise GitError(str(e)) from e

    def file_content_at_commit(self, relative_path: str, sha: str) -> str:
        try:
            repo = Repo(self._workspace_path)
            commit = repo[sha.encode("ascii")]
            # repo[<commit sha>] yields a Commit; anything else is a bad sha
            # and surfaces as GitError via the except below.
            assert isinstance(commit, Commit)
            _, blob_sha = tree_lookup_path(repo.get_object, commit.tree, relative_path.encode())
            blob = repo[blob_sha]
            assert isinstance(blob, Blob)
            return blob.data.decode("utf-8")
        except Exception as e:
            raise GitError(str(e)) from e
