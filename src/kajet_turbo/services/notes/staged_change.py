"""One staged-change/rollback contract shared by every workspace write path.

Generalizes ``StagedWrite``: restore is derived from a byte snapshot taken before ``apply``
runs, not supplied by the caller, so a rollback is byte-exact by construction. ``add``/
``remove`` also make a rename expressible as one commit instead of two.
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session

from kajet_turbo import perf
from kajet_turbo.repositories.git import GitError, GitRepository
from kajet_turbo.repositories.notes import NoteRepository


@dataclass(frozen=True, slots=True)
class StagedChange:
    """One item in a ``staged_workspace_change`` batch.

    ``add`` is the workspace-relative path present after ``apply()`` runs, ``remove`` the one
    absent afterwards — a plain write sets only ``add``, a delete only ``remove``, and a
    rename sets both. ``apply`` owns the working tree and may do anything (write a file,
    unlink, ``Path.rename``); the byte snapshot below is what makes rollback correct
    regardless of what it does.
    """

    add: str | None
    remove: str | None
    apply: Callable[[], None]


@contextmanager
def staged_workspace_change(
    repo: GitRepository, items: Sequence[StagedChange], message: str
) -> Iterator[None]:
    """Snapshot every add/remove path's bytes, apply every item, commit once, and restore
    every snapshot on failure.

    The snapshot is taken for every item up front, before any ``apply()`` runs — not just the
    ones already applied when a mid-batch failure hits. An item whose ``apply()`` never ran
    has an untouched path, so restoring it is a no-op; this is what makes a plain "restore
    everything snapshotted" correct without tracking which items actually applied.
    """
    workspace_path = repo.workspace_path
    snapshots: list[tuple[str, bytes | None]] = []
    for item in items:
        for rel in filter(None, (item.add, item.remove)):
            full = Path(workspace_path, rel)
            snapshots.append((rel, full.read_bytes() if full.exists() else None))
    try:
        for item in items:
            item.apply()
        yield
        repo.commit_changes(
            removed=[item.remove for item in items if item.remove],
            added=[item.add for item in items if item.add],
            message=message,
        )
    except GitError, OSError:
        # Every snapshot reflects pre-batch state (taken before any apply() ran), so
        # restore order doesn't matter — this isn't an undo stack.
        for rel, data in snapshots:
            full = Path(workspace_path, rel)
            if data is None:
                full.unlink(missing_ok=True)
            else:
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_bytes(data)
        raise


def commit_rows_then_tree(
    crud_repo: NoteRepository,
    git_repo: GitRepository,
    items: Sequence[StagedChange],
    message: str,
    *,
    operation: str,
    write_rows: Callable[[Session], None],
    **operation_fields: object,
) -> None:
    """Write DB rows, then commit the tree, both inside one transaction that commits last.

    The invariant (#155): the file tree is the source of truth and ``notes`` rows are a
    derived index, so a write must never be able to leave the index ahead of the tree. Rows
    go in first because SQL is cheap to fail before anything has touched disk; the git commit
    goes last, inside the same transaction, so a ``GitError``/``OSError`` rolls the rows back
    and ``staged_workspace_change`` restores the tree — nothing moved. The only residual
    window is "git committed, SQLite COMMIT failed", which leaves the tree ahead of the
    index; ``reconcile_paths`` heals that direction.
    """
    with crud_repo.operation(operation, **operation_fields) as op, op.session.begin():
        write_rows(op.session)
        # write_rows only calls session.add() (insert_in_session/update_in_session), so a
        # constraint violation would otherwise surface at COMMIT time — i.e. after the git
        # commit below already landed, defeating the ordering this helper exists for.
        op.session.flush()
        with perf.excluded_from("db_ms"), staged_workspace_change(git_repo, items, message):
            pass
