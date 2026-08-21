import contextlib
import fcntl
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from dulwich import porcelain
from dulwich.diff_tree import TreeChange
from dulwich.errors import NotGitRepository
from dulwich.object_store import tree_lookup_path
from dulwich.objects import Blob, Commit
from dulwich.repo import Repo
from nanoid import generate

from kajet_turbo.log import logger
from kajet_turbo.perf import record

COMMITTER = b"Kajet <bot@kajet.app>"

_post_commit_hooks: list[Callable[[str], None]] = []


def register_post_commit_hook(fn: Callable[[str], None]) -> None:
    """Register a callback fired with the workspace path after each successful
    commit. Used to enqueue auto-push. Hook exceptions are logged, not propagated —
    a failing hook must never break the commit."""
    _post_commit_hooks.append(fn)


def _fire_post_commit(workspace_path: str) -> None:
    for hook in _post_commit_hooks:
        try:
            hook(workspace_path)
        except Exception as e:
            logger.warning("post_commit_hook_failed", error=str(e))


def _flat_changes(entry) -> Iterator[TreeChange]:
    """entry.changes() is list[TreeChange] for linear commits and
    list[list[TreeChange]] for merges; yield a flat stream of TreeChange."""
    for item in entry.changes():
        if isinstance(item, list):
            yield from item
        else:
            yield item


def _matching_followed(followed: Iterable[bytes], changed_path: bytes) -> list[bytes]:
    """All followed paths matched by ``changed_path``, replicating dulwich
    Walker._path_matches: exact match or directory-prefix match at a "/" edge.
    Returns EVERY match, not just the first — in the pathological
    file<->directory case (file a.md deleted, then a.md/b.md added) a single
    TreeChange matches "a.md/b.md" exactly AND "a.md" by prefix, and a per-path
    walk yields the commit for both, so the shared walk must credit both."""
    return [
        f
        for f in followed
        if changed_path == f or (changed_path.startswith(f) and changed_path[len(f)] == ord("/"))
    ]


_REPO_LOCKS: dict[str, threading.Lock] = {}
_REPO_LOCKS_GUARD = threading.Lock()


def _lock_key(workspace_path: str) -> str:
    return str(Path(workspace_path).resolve())


def _repo_lock(workspace_path: str) -> threading.Lock:
    key = _lock_key(workspace_path)
    with _REPO_LOCKS_GUARD:
        lock = _REPO_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _REPO_LOCKS[key] = lock
        return lock


class GitError(Exception):
    pass


_LOCK_TIMEOUT = float(os.getenv("KAJET_GIT_LOCK_TIMEOUT", "10"))


@contextlib.contextmanager
def _cross_process_lock(workspace_path: str):
    """Advisory flock serializing git writes across processes/containers.

    Kernel-enforced and auto-released on process death, so a crashed writer never
    wedges the repo (unlike a stale .lock file). Requires a shared local
    filesystem — already guaranteed (both roles mount the same /workspaces volume
    on one host; SQLite WAL needs the same). The lock file lives inside .git so it
    is per-workspace, not enumerated as a workspace, and never committed; dulwich
    ignores it (it uses its own <name>.lock protocol)."""
    lock_path = Path(workspace_path, ".git", "kajet-write.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        deadline = time.monotonic() + _LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise GitError(f"workspace busy (git lock timeout): {workspace_path}") from None
                time.sleep(0.05)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextlib.contextmanager
def _workspace_lock(workspace_path: str):
    """One thread per process (cheap, no flock spin) + one process globally.

    Centrally feeds the perf span: time spent acquiring the lock vs. time holding it
    doing git work — so e.g. edit_note's per-backlink commit loop shows its lock cost.
    """
    t0 = time.monotonic()
    with _repo_lock(workspace_path), _cross_process_lock(workspace_path):
        record("git_lock_wait_ms", (time.monotonic() - t0) * 1000)
        t1 = time.monotonic()
        try:
            yield
        finally:
            record("git_ms", (time.monotonic() - t1) * 1000)


class GitRepository:
    def __init__(self, workspace_path: str) -> None:
        self._workspace_path = workspace_path
        try:
            # Opened once and reused by the read methods below. Reuse is safe for
            # freshness: dulwich reads refs from disk on each access and discovers
            # new loose/pack objects on cache miss, so commits made after
            # construction are still visible. Batch callers (get_many, edit_many,
            # delete_many) rely on this — one open per batch, not per note.
            self._repo = Repo(workspace_path)
        except (NotGitRepository, Exception) as e:
            raise GitError(str(e)) from e

    @classmethod
    def init(cls, path: str) -> GitRepository:
        porcelain.init(path)
        # dulwich defaults HEAD to refs/heads/master; point it at main before the
        # first commit so new workspaces use main (matches the default branch on
        # GitHub/Gitea mirrors). current_branch reads HEAD, so push is unaffected.
        Repo(path).refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")  # ty: ignore[invalid-argument-type] - Literal[bytes] satisfies Ref type
        return cls(path)

    def commit_file(self, relative_path: str, message: str) -> None:
        with _workspace_lock(self._workspace_path):
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
        _fire_post_commit(self._workspace_path)

    def delete_file(self, relative_path: str, message: str) -> None:
        with _workspace_lock(self._workspace_path):
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
        _fire_post_commit(self._workspace_path)

    def delete_files(self, relative_paths: list[str], message: str) -> None:
        """Unstage and commit removal of multiple files in a single commit (one lock,
        one ref update). No-op on an empty list.
        """
        if not relative_paths:
            return
        with _workspace_lock(self._workspace_path):
            try:
                for rel in relative_paths:
                    Path(self._workspace_path, rel).unlink(missing_ok=True)
                porcelain.rm(self._workspace_path, paths=list(relative_paths))
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
        _fire_post_commit(self._workspace_path)

    def rename_file(self, old_rel: str, new_rel: str, message: str) -> None:
        with _workspace_lock(self._workspace_path):
            old_full = Path(self._workspace_path, old_rel)
            new_full = Path(self._workspace_path, new_rel)
            try:
                if not old_full.exists():
                    raise GitError(f"File not found: {old_rel}")
                if new_full.exists():
                    raise GitError(f"File already exists: {new_rel}")
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
                if new_full.exists() and not old_full.exists():
                    old_full.parent.mkdir(parents=True, exist_ok=True)
                    new_full.rename(old_full)
                raise GitError(str(e)) from e
        _fire_post_commit(self._workspace_path)

    def commit_moves(self, removed_rels: list[str], added_rels: list[str], message: str) -> None:
        """Record a set of moved files in a single commit: drop ``removed_rels`` from the
        index and add ``added_rels``. The caller has already done the filesystem moves
        (folder move uses a temp dir), so this only reconciles git state."""
        if not removed_rels and not added_rels:
            return
        with _workspace_lock(self._workspace_path):
            try:
                if removed_rels:
                    # cached=True: drop from the index only. The caller already moved the
                    # files on disk; a working-tree rm would, on a case-insensitive FS,
                    # delete the just-created destination (old/new paths share an inode).
                    porcelain.rm(self._workspace_path, paths=list(removed_rels), cached=True)
                if added_rels:
                    porcelain.add(self._workspace_path, paths=list(added_rels))
                porcelain.commit(
                    self._workspace_path,
                    message=message.encode(),
                    author=COMMITTER,
                    committer=COMMITTER,
                )
            except Exception as e:
                raise GitError(str(e)) from e
        _fire_post_commit(self._workspace_path)

    def commit_files(self, relative_paths: list[str], message: str) -> None:
        """Stage and commit multiple files in a single commit (one lock, one ref update).

        Used by batch note creation; the caller has already written the files to disk.
        No-op on an empty list. Raises GitError if any path is missing on disk.
        """
        if not relative_paths:
            return
        with _workspace_lock(self._workspace_path):
            try:
                for rel in relative_paths:
                    if not Path(self._workspace_path, rel).exists():
                        raise GitError(f"File not found: {rel}")
                porcelain.add(self._workspace_path, paths=list(relative_paths))
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
        _fire_post_commit(self._workspace_path)

    def rename_master_to_main(self) -> bool:
        """Idempotently move this repo from ``master`` to ``main``: point HEAD at
        main and rename the branch ref. No-op (returns False) if HEAD is not on
        master. Holds the workspace lock — it mutates refs."""
        with _workspace_lock(self._workspace_path):
            repo = Repo(self._workspace_path)
            head = repo.refs.read_ref(b"HEAD")  # ty: ignore[invalid-argument-type] - Literal[bytes] satisfies Ref type
            if head != b"ref: refs/heads/master":
                return False
            if b"refs/heads/master" in repo.refs:  # ty: ignore[unsupported-operator] - dulwich RefsContainer supports __contains__
                repo.refs[b"refs/heads/main"] = repo.refs[b"refs/heads/master"]  # ty: ignore[invalid-assignment,invalid-argument-type] - Literal[bytes] satisfies Ref type
            repo.refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")  # ty: ignore[invalid-argument-type] - Literal[bytes] satisfies Ref type
            if b"refs/heads/master" in repo.refs:  # ty: ignore[unsupported-operator] - dulwich RefsContainer supports __contains__
                del repo.refs[b"refs/heads/master"]  # ty: ignore[invalid-argument-type] - Literal[bytes] satisfies Ref type
            return True

    def last_commit_time(self) -> int | None:
        try:
            walker = self._repo.get_walker(max_entries=1)
            for entry in walker:
                return entry.commit.author_time
            return None
        except Exception as e:
            logger.opt(exception=e).warning(
                "last_commit_time_failed", workspace=self._workspace_path
            )
            return None

    def file_history(self, relative_path: str, limit: int = 50) -> list[dict]:
        return self.file_histories([relative_path], limit)[relative_path]

    def file_histories(self, relative_paths: list[str], limit: int) -> dict[str, list[dict]]:
        """Up to ``limit`` newest history entries per path, resolved in ONE walk.

        Semantically identical to {p: file_history(p, limit)} run independently —
        a live walk from HEAD, no caching; dulwich counts max_entries only on
        commits that pass the path filter, and the per-path countdown here counts
        matches the same way. The cost is the walk depth needed by the
        slowest-to-resolve path instead of the sum over all paths: each path
        starts with a budget of ``limit`` entries, a matching commit decrements
        it, an exhausted path stops being followed, and the walk stops as soon as
        every budget hits zero. No global max_entries cap — a capped shared walk
        could return fewer entries than the per-path walk would, changing
        semantics. Paths never touched by any commit map to []. ``limit`` has no
        default: this is the shared mechanism behind both file_history (default
        50) and head_shas_for_paths (always 1) — a default here would just be
        one wrapper's preference leaking into the general primitive.
        """
        result: dict[str, list[dict]] = {p: [] for p in relative_paths}
        by_bytes = {p.encode(): p for p in relative_paths}
        remaining = dict.fromkeys(by_bytes, limit)
        if not remaining or limit <= 0:
            return result  # guard: paths=[] would mean "no filter" to dulwich
        try:
            walker = self._repo.get_walker(paths=list(remaining))
            for entry in walker:
                # A commit is one history entry per matched path no matter how
                # many of its changes touch that path (e.g. a modify carries the
                # same path in both change.new and change.old) — collect the
                # distinct changed paths first, so a modify only gets matched once.
                changed_paths = {
                    tree_entry.path
                    for change in _flat_changes(entry)
                    for tree_entry in (change.new, change.old)
                    if tree_entry is not None
                }
                matched: set[bytes] = set()
                for changed_path in changed_paths:
                    matched.update(_matching_followed(remaining, changed_path))
                if not matched:
                    continue
                history_entry = {
                    "sha": entry.commit.id.decode("ascii"),
                    "message": entry.commit.message.decode("utf-8", errors="replace").strip(),
                    "timestamp": entry.commit.author_time,
                }
                for followed in matched:
                    result[by_bytes[followed]].append(history_entry)
                    remaining[followed] -= 1
                    if remaining[followed] == 0:
                        del remaining[followed]
                if not remaining:
                    break  # early exit — the whole point of the shared walk
        except KeyError:
            return result  # empty repo (no HEAD) — mirrors the per-path walk's []
        except Exception as e:
            raise GitError(str(e)) from e
        return result

    def head_shas_for_paths(self, relative_paths: list[str]) -> dict[str, str | None]:
        """Sha of the most recent commit touching each path, resolved in ONE walk.

        Semantically identical to {p: file_history(p, limit=1)[0]["sha"] or None}
        — file_histories does the shared walk with a per-path budget of one entry.
        Paths never touched by any commit map to None.
        """
        histories = self.file_histories(relative_paths, limit=1)
        return {p: (h[0]["sha"] if h else None) for p, h in histories.items()}

    def file_content_at_commit(self, relative_path: str, sha: str) -> str:
        try:
            repo = self._repo
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


def delete_workspace_tree(workspace_path: str) -> None:
    """Removes a workspace's git repo from disk. Idempotent (no-op if already gone,
    so a retried WorkspaceService.delete is safe).

    Renames off the canonical path under _workspace_lock instead of rmtree-ing in
    place: _cross_process_lock reopens its lock file by path (os.open(..., O_CREAT))
    on every acquisition, so an in-place rmtree racing a writer in a *different*
    process could unlink the lock file mid-delete, letting that writer mint a fresh
    inode, flock it uncontended, and write into the tree we're deleting — invisible
    to us. After the rename, workspace_path no longer exists, so a would-be writer's
    O_CREAT open fails fast (no parent dir) instead of silently succeeding. The
    actual rmtree runs outside the lock, against the now-unreachable trash path.
    """
    path = Path(workspace_path)
    if not path.exists():
        return
    trash = path.parent / f".trash-{path.name}-{generate()}"
    try:
        with _workspace_lock(workspace_path):
            path.rename(trash)
    except FileNotFoundError:
        # A concurrent delete_workspace_tree call already renamed this path away
        # between our exists() check and the lock/rename above — already gone.
        return
    with _REPO_LOCKS_GUARD:
        _REPO_LOCKS.pop(_lock_key(workspace_path), None)
    try:
        shutil.rmtree(trash)
    except OSError as e:
        # Best-effort reclaim: the workspace is already gone from the DB and from
        # its canonical path, so this failure doesn't undo the delete — but it must
        # not be silent, or a leftover .trash-* directory becomes invisible forever.
        logger.warning("workspace_trash_reclaim_failed", trash=str(trash), error=str(e))
