import contextlib
import fcntl
import inspect
import os
import shutil
import stat
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextvars import ContextVar, Token
from dataclasses import dataclass
from functools import wraps
from pathlib import Path

from dulwich import porcelain
from dulwich.diff_tree import TreeChange
from dulwich.errors import NotGitRepository
from dulwich.object_store import iter_tree_contents, tree_lookup_path
from dulwich.objects import Blob, Commit
from dulwich.repo import Repo
from nanoid import generate

from kajet_turbo.log import logger
from kajet_turbo.perf import record, timed

COMMITTER = b"Kajet <bot@kajet.app>"


class PostCommitHooks:
    """Callbacks owned by one application resource graph."""

    def __init__(self) -> None:
        self._hooks: list[Callable[[str], None]] = []

    def register(self, fn: Callable[[str], None]) -> None:
        if fn not in self._hooks:
            self._hooks.append(fn)

    def fire(self, workspace_path: str) -> None:
        for hook in self._hooks:
            try:
                hook(workspace_path)
            except Exception as e:
                logger.warning("post_commit_hook_failed", error=str(e))


_CURRENT_HOOKS: ContextVar[PostCommitHooks | None] = ContextVar(
    "kajet_post_commit_hooks", default=None
)


@contextlib.contextmanager
def use_post_commit_hooks(hooks: PostCommitHooks):
    token: Token[PostCommitHooks | None] = _CURRENT_HOOKS.set(hooks)
    try:
        yield
    finally:
        _CURRENT_HOOKS.reset(token)


def _fire_post_commit(workspace_path: str, hooks: PostCommitHooks | None) -> None:
    if hooks is None:
        return
    hooks.fire(workspace_path)


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


_REPO_LOCKS: dict[str, threading.RLock] = {}
_REPO_LOCKS_GUARD = threading.Lock()
_LOCK_STATE = threading.local()


class _TransactionState:
    def __init__(self) -> None:
        self.post_commit_hooks: PostCommitHooks | None = None
        self.post_release: list[Callable[[], None]] = []


def _lock_key(workspace_path: str) -> str:
    return str(Path(workspace_path).resolve())


def _repo_lock(workspace_path: str) -> threading.RLock:
    key = _lock_key(workspace_path)
    with _REPO_LOCKS_GUARD:
        lock = _REPO_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _REPO_LOCKS[key] = lock
        return lock


def _transaction_states() -> dict[str, _TransactionState]:
    states = getattr(_LOCK_STATE, "states", None)
    if states is None:
        states = {}
        _LOCK_STATE.states = states
    return states


def _record_post_commit(workspace_path: str, hooks: PostCommitHooks | None) -> None:
    """Queue one hook notification for the outermost workspace transaction."""
    state = _transaction_states().get(_lock_key(workspace_path))
    if state is None:
        # Defensive fallback: callers normally record while holding _workspace_lock.
        _fire_post_commit(workspace_path, hooks)
    else:
        state.post_commit_hooks = hooks


def defer_workspace_postprocess(workspace_path: str, callback: Callable[[], None]) -> None:
    """Run ``callback`` synchronously after the outer workspace lock is released.

    Service methods use this for derived work such as search indexing: callers still
    observe failures before the method returns, but another writer is not held behind
    CPU work or unrelated database/job-queue I/O. Outside a transaction the callback
    runs immediately, keeping the helper safe for shared lower-level code.
    """
    state = _transaction_states().get(_lock_key(workspace_path))
    if state is None:
        callback()
    else:
        state.post_release.append(callback)


class GitError(Exception):
    pass


@dataclass(frozen=True)
class GitSnapshot:
    """An immutable commit selected for an export."""

    sha: str
    timestamp: int


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
    """One writer per workspace across threads and processes, reentrant per thread.

    Only the outermost acquisition opens and flocks the lock file. Nested commit helpers
    reuse the transaction held by their caller, avoiding both RLock and flock deadlocks.
    Lock perf fields, post-commit hooks, and deferred postprocessing are likewise handled
    once for the outer transaction.
    """
    key = _lock_key(workspace_path)
    states = _transaction_states()
    nested = key in states
    pending_post_commit_hooks: PostCommitHooks | None = None
    post_release: list[Callable[[], None]] = []
    t0 = time.monotonic()
    try:
        with _repo_lock(workspace_path):
            if nested:
                yield
                return

            state = _TransactionState()
            states[key] = state
            try:
                with _cross_process_lock(workspace_path):
                    record("git_lock_wait_ms", (time.monotonic() - t0) * 1000)
                    t1 = time.monotonic()
                    try:
                        yield
                    finally:
                        record("workspace_write_ms", (time.monotonic() - t1) * 1000)
            finally:
                pending_post_commit_hooks = state.post_commit_hooks
                post_release = state.post_release
                states.pop(key, None)
    finally:
        if not nested:
            # Run external side effects only after both locks have been released. Multiple
            # commits in one transaction intentionally coalesce into one auto-push enqueue.
            # The hook goes first so a deferred indexing failure cannot suppress auto-push.
            if pending_post_commit_hooks is not None:
                _fire_post_commit(workspace_path, pending_post_commit_hooks)
            for callback in post_release:
                callback()


@contextlib.contextmanager
def _git_write(workspace_path: str):
    """Lock one Git mutation and account only its Dulwich/filesystem work as git_ms."""
    with _workspace_lock(workspace_path), timed("git_ms"):
        yield


def workspace_write_transaction[**P, T](fn: Callable[P, T]) -> Callable[P, T]:
    """Wrap a synchronous service method's full workspace mutation in one lock.

    The method must expose a ``ws_path`` argument. Binding by signature keeps the
    decorator correct for both positional and keyword calls without duplicating a
    fragile parameter index at every call site.
    """
    signature = inspect.signature(fn)
    if "ws_path" not in signature.parameters:
        raise TypeError("workspace_write_transaction requires a ws_path parameter")

    @wraps(fn)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        bound = signature.bind(*args, **kwargs)
        workspace_path = str(bound.arguments["ws_path"])
        with _workspace_lock(workspace_path):
            return fn(*args, **kwargs)

    return wrapped


class GitRepository:
    def __init__(self, workspace_path: str, hooks: PostCommitHooks | None = None) -> None:
        self._workspace_path = workspace_path
        self._hooks = hooks if hooks is not None else _CURRENT_HOOKS.get()
        try:
            # Opened once and reused by the read methods below. Reuse is safe for
            # freshness: dulwich reads refs from disk on each access and discovers
            # new loose/pack objects on cache miss, so commits made after
            # construction are still visible. Batch callers (get_many, edit_many,
            # delete_many) rely on this — one open per batch, not per note.
            self._repo = Repo(workspace_path)
        except (NotGitRepository, Exception) as e:
            raise GitError(str(e)) from e

    @property
    def workspace_path(self) -> str:
        return self._workspace_path

    @classmethod
    def init(cls, path: str, hooks: PostCommitHooks | None = None) -> GitRepository:
        porcelain.init(path)
        # dulwich defaults HEAD to refs/heads/master; point it at main before the
        # first commit so new workspaces use main (matches the default branch on
        # GitHub/Gitea mirrors). current_branch reads HEAD, so push is unaffected.
        Repo(path).refs.set_symbolic_ref(b"HEAD", b"refs/heads/main")  # ty: ignore[invalid-argument-type] - Literal[bytes] satisfies Ref type
        return cls(path, hooks)

    @contextlib.contextmanager
    def transaction(self) -> Iterator[GitRepository]:
        """Serialize a workspace read-modify-write sequence through its final commit."""
        with _workspace_lock(self._workspace_path):
            yield self

    def commit_changes(self, *, removed: list[str], added: list[str], message: str) -> None:
        """One commit recording ``removed`` dropped from the index and ``added`` staged.

        Removals are INDEX-ONLY (``cached=True``): the caller owns the working tree and
        has already unlinked / moved / renamed the files on disk — a working-tree rm
        would, on a case-insensitive FS, delete a just-created destination that shares an
        inode with a removed source. No-op when both lists are empty. ``added`` paths
        must already exist on disk, or this raises GitError.
        """
        if not removed and not added:
            return
        with _git_write(self._workspace_path):
            try:
                for rel in added:
                    if not Path(self._workspace_path, rel).exists():
                        raise GitError(f"File not found: {rel}")
                if removed:
                    porcelain.rm(self._workspace_path, paths=list(removed), cached=True)
                if added:
                    porcelain.add(self._workspace_path, paths=list(added))
                porcelain.commit(
                    self._workspace_path,
                    message=message.encode(),
                    author=COMMITTER,
                    committer=COMMITTER,
                )
                _record_post_commit(self._workspace_path, self._hooks)
            except GitError:
                raise
            except Exception as e:
                raise GitError(str(e)) from e

    def commit_file(self, relative_path: str, message: str) -> None:
        self.commit_files([relative_path], message)

    def commit_files(self, relative_paths: list[str], message: str) -> None:
        """Stage and commit multiple files in a single commit (one lock, one ref update).

        Used by batch note creation; the caller has already written the files to disk.
        No-op on an empty list. Raises GitError if any path is missing on disk.
        """
        self.commit_changes(removed=[], added=list(relative_paths), message=message)

    def delete_file(self, relative_path: str, message: str) -> None:
        self.delete_files([relative_path], message)

    def delete_files(self, relative_paths: list[str], message: str) -> None:
        """Unlink files on disk and commit their removal in a single commit (one lock,
        one ref update). No-op on an empty list. Any failure — including an OS-level
        unlink error, not just a git error — surfaces as GitError.
        """
        if not relative_paths:
            return
        try:
            for rel in relative_paths:
                Path(self._workspace_path, rel).unlink(missing_ok=True)
            self.commit_changes(removed=list(relative_paths), added=[], message=message)
        except GitError:
            raise
        except Exception as e:
            raise GitError(str(e)) from e

    def rename_master_to_main(self) -> bool:
        """Idempotently move this repo from ``master`` to ``main``: point HEAD at
        main and rename the branch ref. No-op (returns False) if HEAD is not on
        master. Holds the workspace lock — it mutates refs."""
        with _git_write(self._workspace_path):
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

    def head_snapshot(self) -> GitSnapshot | None:
        """Return the current HEAD commit identity, or ``None`` for an empty repo."""
        try:
            commit_id = self._repo.head()
            commit = self._repo[commit_id]
            assert isinstance(commit, Commit)
            return GitSnapshot(sha=commit.id.decode("ascii"), timestamp=commit.commit_time)
        except KeyError:
            return None
        except Exception as e:
            raise GitError(str(e)) from e

    def write_snapshot_files(
        self, snapshot: GitSnapshot, write_file: Callable[[str, bytes, int], None]
    ) -> None:
        """Send regular files from ``snapshot`` to ``write_file`` in Git tree order.

        The caller owns archive formatting. Reading blobs by the selected commit
        rather than the worktree prevents temporary or uncommitted filesystem
        changes from leaking into an export.
        """
        try:
            commit = self._repo[snapshot.sha.encode("ascii")]
            assert isinstance(commit, Commit)
            for entry in iter_tree_contents(self._repo.object_store, commit.tree):
                if entry.mode is None or not stat.S_ISREG(entry.mode):
                    continue
                assert entry.path is not None and entry.sha is not None
                blob = self._repo[entry.sha]
                assert isinstance(blob, Blob)
                relative_path = entry.path.decode("utf-8", errors="surrogateescape")
                write_file(relative_path, blob.data, entry.mode)
        except Exception as e:
            raise GitError(str(e)) from e

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
