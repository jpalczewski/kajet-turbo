"""Shared helpers for tests/services/ — see tests/CLAUDE.md: "A helper needed by a second
file moves to the suite's helpers.py — it does not get copied."
"""

from pathlib import Path

from kajet_turbo.repositories.git import GitRepository


def head_sha(workspace, relative_path: str) -> str:
    """The current HEAD commit sha touching ``relative_path``."""
    return GitRepository(str(workspace)).file_history(relative_path, limit=1)[0]["sha"]


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
    from kajet_turbo.repositories.notes import NoteChunkRepository
    from kajet_turbo.services.indexing import NoteIndexer
    from tests.services.conftest import build_note_service

    chunk_repo = NoteChunkRepository(database.engine)
    indexer = NoteIndexer(
        chunk_repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda owner_id: None,
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
