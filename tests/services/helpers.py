"""Shared helpers for tests/services/ — see tests/CLAUDE.md: "A helper needed by a second
file moves to the suite's helpers.py — it does not get copied."
"""

import json
import re
from pathlib import Path

from kajet_turbo.markdown import EditMode
from kajet_turbo.repositories.git import GitRepository


def head_sha(workspace, relative_path: str) -> str:
    """The current HEAD commit sha touching ``relative_path``."""
    return GitRepository(str(workspace)).file_history(relative_path, limit=1)[0]["sha"]


def corrupt_temporal_field(path: str, field: str, bad_value: str) -> None:
    """Hand-edit a saved note's file so ``field`` (``occurred_at``/``period``) becomes
    unparseable, bypassing ``NoteFrontmatter``'s own validation — simulates the external
    corruption #132's write-path regressions need to reproduce. Regex, not a plain string
    replace, so it works regardless of PyYAML's quoting for the existing value."""
    text = Path(path).read_text()
    corrupted, n = re.subn(rf"(?m)^{field}:.*$", f"{field}: {bad_value}", text, count=1)
    assert n == 1, f"{field!r} line not found in {path!r} to corrupt"
    Path(path).write_text(corrupted)


def edit_item(
    note_id: str,
    expected_sha: str = "",
    *,
    mode: EditMode = "append",
    content: str | None = None,
    old_str: str | None = None,
    new_str: str | None = None,
    target_heading: str | None = None,
    replace_all: bool = False,
    tags: list[str] | None = None,
    occurred_at: str | None = None,
    period: str | None = None,
    clear_date_metadata: bool = False,
):
    """One ``edit_many()`` batch item, built from flat kwargs instead of hand-nesting
    ``EditBatchItem(edit=EditSpec(...))`` at every call site."""
    from kajet_turbo.markdown import EditSpec
    from kajet_turbo.services.notes import EditBatchItem

    return EditBatchItem(
        note_id=note_id,
        expected_sha=expected_sha,
        edit=EditSpec(
            mode=mode,
            content=content,
            old_str=old_str,
            new_str=new_str,
            target_heading=target_heading,
            replace_all=replace_all,
        ),
        tags=tags,
        occurred_at=occurred_at,
        period=period,
        clear_date_metadata=clear_date_metadata,
    )


def build_reindex_handler(database, workspaces_dir: str, jobs=None):
    """A ``ReindexNoteHandler`` wired to the same engine a ``build_note_service`` /
    ``service`` fixture uses, for tests that need to drain the ``reindex_note`` jobs a
    batch write or backlink rewrite now enqueues instead of chunking inline.

    ``workspaces_dir`` must be the directory such that
    ``<workspaces_dir>/<owner_id>/<workspace_name>`` is the note's on-disk workspace root
    (the same layout ``workspace_path()`` expects) — see ``drain_reindex_jobs``.
    """
    from kajet_turbo.repositories.jobs import JobRepository
    from kajet_turbo.repositories.notes import NoteChunkRepository, NoteRepository
    from kajet_turbo.services.reindex_handler import ReindexNoteHandler

    if jobs is None:
        jobs = JobRepository(database.engine)
    return ReindexNoteHandler(
        note_repo=NoteRepository(database.engine),
        chunk_repo=NoteChunkRepository(database.engine),
        jobs=jobs,
        resolve_cfg=lambda owner_id: None,
        workspaces_dir=workspaces_dir,
    )


def drain_reindex_jobs(jobs, handler, owner_id: str, workspace_name: str) -> int:
    """Run every pending ``reindex_note`` job for ``(owner_id, workspace_name)`` through
    ``handler`` synchronously and mark it complete — the test-side stand-in for the real
    worker loop, so a test can assert post-batch chunk/FTS state without a real worker.
    Returns how many jobs were drained."""
    pending = [
        job
        for job in jobs.list_jobs(owner_id, kind="reindex_note", status="pending")
        if json.loads(job.payload)["workspace"] == workspace_name
    ]
    for job in pending:
        handler(json.loads(job.payload))
        jobs.complete(job.id)
    return len(pending)


def build_reconcile_wiring(database, base: Path):
    """A NoteService wired with a real LinkReconcileRepository + DanglingLinkRepository,
    plus the ReconcileLinksHandler that can drain the jobs it enqueues — for tests that
    need to observe dangling-link healing end to end (mark_and_enqueue -> handler ->
    link graph), not just that a job got queued."""
    from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
    from kajet_turbo.repositories.jobs import JobRepository
    from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
    from kajet_turbo.repositories.notes import NoteRepository
    from kajet_turbo.services.reconcile_links_handler import ReconcileLinksHandler
    from tests.services.conftest import build_note_service

    jobs = JobRepository(database.engine)
    dirty = LinkReconcileRepository(database.engine, jobs)
    dangling = DanglingLinkRepository(database.engine)
    service = build_note_service(
        database,
        link_validation_enabled=lambda _ws, _owner: False,
        dangling_repo=dangling,
        reconcile_repo=dirty,
    )
    handler = ReconcileLinksHandler(
        NoteRepository(database.engine),
        service._link_service,
        dangling,
        dirty,
        str(base),
    )
    return service, jobs, dirty, dangling, handler


def make_service_with_dangling(database, link_validation_enabled=None):
    """Build a NoteService wired with a real DanglingLinkRepository on the same engine."""
    from kajet_turbo.embedding.cache import EmbeddingCacheRepository
    from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
    from kajet_turbo.repositories.jobs import JobRepository
    from kajet_turbo.repositories.notes import NoteChunkRepository
    from kajet_turbo.services.indexing import NoteIndexer
    from tests.services.conftest import build_note_service

    chunk_repo = NoteChunkRepository(database.engine)
    indexer = NoteIndexer(
        chunk_repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda owner_id: None,
        jobs=JobRepository(database.engine),
    )
    dangling = DanglingLinkRepository(database.engine)
    return (
        build_note_service(
            database,
            indexer=indexer,
            link_validation_enabled=link_validation_enabled,
            dangling_repo=dangling,
        ),
        dangling,
    )


def make_flaky_write(real_write, *, fail_on_call: int = 2, message: str = "disk full"):
    """A ``write_note_file`` stand-in that raises ``OSError`` on the Nth call, delegating to
    ``real_write`` otherwise.

    Used to pin the #104 acceptance behavior: a write failing partway through a batch must
    roll back every file already written and make no commit.
    """
    calls = {"n": 0}

    def flaky_write(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == fail_on_call:
            raise OSError(message)
        return real_write(*args, **kwargs)

    return flaky_write


def make_flaky_db_write(
    real_fn,
    *,
    fail_on_call: int = 1,
    message: str = "db exploded",
    exc: type[Exception] = RuntimeError,
):
    """A stand-in for any callable (``insert_in_session``/``update_in_session``, or
    ``GitRepository.commit_changes`` for the symmetric git-side case) that raises ``exc`` on
    the Nth call, delegating to ``real_fn`` otherwise.

    Used to pin #155's ordering: a failure on either side of ``commit_rows_then``/
    ``commit_rows_then_tree`` must abort before the other side runs, leaving neither the tree
    nor the rows touched. Works unchanged for a bound method captured off the class before
    patching (e.g. ``real_fn = GitRepository.commit_changes``) — ``self`` arrives as
    ``args[0]`` when the wrapper replaces the class attribute, and is forwarded through.
    """
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == fail_on_call:
            raise exc(message)
        return real_fn(*args, **kwargs)

    return flaky
