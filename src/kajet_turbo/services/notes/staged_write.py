"""One staged-write/rollback contract shared by every note write path.

Generalizes rename_tag's original contract (the only call site that got this right): an
item is recorded as written *before* its write runs, so a mid-batch ``OSError`` rolls back
exactly like a ``GitError`` from the commit — never a subset silently left on disk.
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from kajet_turbo.repositories.git import GitError, GitRepository


@dataclass(frozen=True, slots=True)
class StagedWrite:
    """One file's write and its own rollback, for staged_note_write's all-or-nothing batch."""

    relative: str
    apply: Callable[[], None]
    restore: Callable[[], None]


@contextmanager
def staged_note_write(
    repo: GitRepository, items: Sequence[StagedWrite], message: str
) -> Iterator[None]:
    """Write every item, commit once, restore every item actually written on failure.

    Takes an already-open ``GitRepository`` rather than a workspace path: opening one
    reopens dulwich's refs/pack indexes, so a caller that already has one (a batch's
    ``_locate_batch`` walk, ``update()``'s rename leg) must reuse it, not open a second —
    see ``test_notes_batch_git_reuse.py`` for the regression this guards against.

    Single-file callers pass a one-element list — ``commit_files`` handles that the same
    as a batch, so there is one code path, not two.
    """
    written: list[StagedWrite] = []
    try:
        for item in items:
            written.append(item)
            item.apply()
        yield
        if items:
            repo.commit_files([item.relative for item in items], message)
    except GitError, OSError:
        for item in written:
            item.restore()
        raise
