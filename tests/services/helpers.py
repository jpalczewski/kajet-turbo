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


def rel_path(ws_path, filepath: str) -> str:
    """``filepath`` relative to the workspace root, as a string."""
    return str(Path(filepath).relative_to(ws_path))


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


def drain_reindex_jobs(
    jobs, handler, owner_id: str, workspace_name: str, *, worker_id: str = "test-drain"
) -> int:
    """Run every runnable ``reindex_note`` job for ``(owner_id, workspace_name)`` through
    ``handler`` synchronously and mark it complete — the test-side stand-in for the real
    worker loop, so a test can assert post-batch chunk/FTS state without a real worker.
    Claims each job first (like ``run_worker`` does) so the fenced complete() path is
    exercised; anything claimed that is not ours is released back afterwards.
    Returns how many jobs were drained."""
    drained = 0
    seen: set[str] = set()
    while True:
        job = jobs.claim(worker_id)
        if job is None or job.id in seen:
            break
        seen.add(job.id)
        if job.kind == "reindex_note" and json.loads(job.payload)["workspace"] == workspace_name:
            handler(json.loads(job.payload))
            assert jobs.complete(job.id, job.locked_by) is True
            drained += 1
    jobs.reset_running_to_pending(worker_id)
    return drained


def build_note_reconcile_service_from(wiring):
    """A NoteReconcileService sharing every repo/collaborator instance a NoteWiring
    already holds — mirrors production wiring (dependencies.py builds both from the
    same repo set and the same NoteTeardown instance, #388), so a test's monkeypatch on
    one of `wiring`'s repo instances is visible to the reconcile side too, and
    reconcile_paths/save() see the same DB state without needing a second,
    independently-constructed set of repos."""
    from kajet_turbo.services.notes import NoteReconcileService

    return NoteReconcileService(
        wiring.crud_repo,
        wiring.tag_repo,
        wiring.link_service,
        wiring.teardown,
        indexer=wiring.indexer,
        reconcile_repo=wiring.reconcile_repo,
    )


def build_note_folder_service_from(wiring):
    """A NoteFolderService sharing every repo/collaborator instance a NoteWiring already
    holds — mirrors production wiring (dependencies.py builds both from the same repo set),
    so a test that patches one of `wiring`'s repo instances (the #155/#170 ordering tests
    patch `update_in_session` and assert it is *not* called) still intercepts the folder
    move made here; a separately constructed folder service would pass those negatives
    vacuously.

    Left without a FolderMetaRepository, matching what the note builder wired internally
    before #229 — so `move_folder`'s folder-meta `rename_paths` step stays unexercised.
    That is a known coverage gap carried over from the delegate era, not a contract."""
    from kajet_turbo.services.notes import NoteFolderService

    return NoteFolderService(
        wiring.crud_repo,
        wiring.link_service,
        reconcile_repo=wiring.reconcile_repo,
    )


def build_reconcile_wiring(database, base: Path):
    """A NoteWiring wired with a real LinkReconcileRepository + DanglingLinkRepository,
    plus the ReconcileLinksHandler that can drain the jobs it enqueues — for tests that
    need to observe dangling-link healing end to end (mark_and_enqueue -> handler ->
    link graph), not just that a job got queued."""
    from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
    from kajet_turbo.repositories.jobs import JobRepository
    from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
    from kajet_turbo.repositories.notes import NoteRepository
    from kajet_turbo.services.reconcile_links_handler import ReconcileLinksHandler
    from tests.services.conftest import build_note_wiring

    jobs = JobRepository(database.engine)
    dirty = LinkReconcileRepository(database.engine, jobs)
    dangling = DanglingLinkRepository(database.engine)
    wiring = build_note_wiring(
        database,
        link_validation_enabled=lambda _ws, _owner: False,
        dangling_repo=dangling,
        reconcile_repo=dirty,
    )
    handler = ReconcileLinksHandler(
        NoteRepository(database.engine),
        wiring.link_service,
        dangling,
        dirty,
        str(base),
    )
    return wiring, wiring.link_service, jobs, dirty, dangling, handler


def make_service_with_dangling(database, link_validation_enabled=None):
    """Build a NoteWiring wired with a real DanglingLinkRepository on the same engine,
    returning the link boundary alongside it for tests that assert on the link graph."""
    from kajet_turbo.embedding.cache import EmbeddingCacheRepository
    from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
    from kajet_turbo.repositories.jobs import JobRepository
    from kajet_turbo.repositories.notes import NoteChunkRepository
    from kajet_turbo.services.indexing import NoteIndexer
    from tests.services.conftest import build_note_wiring

    chunk_repo = NoteChunkRepository(database.engine)
    indexer = NoteIndexer(
        chunk_repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda owner_id: None,
        jobs=JobRepository(database.engine),
    )
    dangling = DanglingLinkRepository(database.engine)
    wiring = build_note_wiring(
        database,
        indexer=indexer,
        link_validation_enabled=link_validation_enabled,
        dangling_repo=dangling,
    )
    return wiring, wiring.link_service, dangling


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


def seed_full_workspace(database, *, user_id: str, name: str) -> None:
    """Seeds one row in every workspace-scoped table, plus a WorkspaceRemote (which
    needs a real User + SshKey to satisfy FKs). Shared by tests covering full-workspace
    teardown (``WorkspaceService.delete`` and ``NoteReconcileService.clear_workspace_data``
    directly)."""
    from sqlmodel import Session

    from kajet_turbo.markdown import Chunk
    from kajet_turbo.models import Note
    from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
    from kajet_turbo.repositories.folder_meta import FolderMetaRepository
    from kajet_turbo.repositories.jobs import JobRepository
    from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
    from kajet_turbo.repositories.note_share_link import NoteShareLinkRepository
    from kajet_turbo.repositories.notes import (
        NoteChunkRepository,
        NoteLinkRepository,
        NoteTagRepository,
    )
    from kajet_turbo.repositories.ssh_keys import SshKeyRepository
    from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository
    from tests.conftest import seed_user

    seed_user(database, user_id)
    with Session(database.engine) as session:
        session.add(
            Note(
                id=f"{user_id}-n1",
                workspace=name,
                owner_id=user_id,
                title="T",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
        )
        session.commit()

    chunk_repo = NoteChunkRepository(database.engine)
    chunk_repo.replace_chunks(
        f"{user_id}-n1", name, user_id, "T", [Chunk(0, ["# T"], "body", 0, 4)], None, None
    )

    NoteTagRepository(database.engine).sync_note_tags(
        f"{user_id}-n1", name, user_id, [("proj/notes", "frontmatter")]
    )

    NoteLinkRepository(database.engine).replace_links(
        f"{user_id}-n1", name, user_id, {f"{user_id}-n2"}
    )

    DanglingLinkRepository(database.engine).replace_for_source(
        f"{user_id}-n1", name, user_id, [("", "Missing Note")]
    )

    share_link_repo = NoteShareLinkRepository(database.engine)
    share_link = share_link_repo.create(f"{user_id}-n1", name, user_id)
    share_link_repo.record_visit(share_link.token, "203.0.113.1", "Browser/1")

    FolderMetaRepository(database.engine).set(user_id, name, "proj", description="Project folder")

    ssh_repo = SshKeyRepository(database.engine)
    key = ssh_repo.create(user_id, "deploy", "ed25519", "ssh-ed25519 AAAA", b"secret", "fp")
    WorkspaceRemoteRepository(database.engine).upsert(
        user_id, name, origin_url="git@host:repo.git", ssh_key_id=key.id, enabled=True
    )

    job_repo = JobRepository(database.engine)
    job_repo.enqueue(
        "push_workspace",
        {"user_id": user_id, "workspace": name, "ws_path": f"/workspaces/{user_id}/{name}"},
        dedup_key=f"{user_id}:{name}",
        user_id=user_id,
    )
    job_repo.enqueue(
        "heal_dangling",
        {"user_id": user_id, "workspace": name},
        dedup_key=f"heal:{user_id}:{name}",
        user_id=user_id,
    )
    job_repo.enqueue(
        "embed_note",
        {"note_id": f"{user_id}-n1", "workspace": name, "owner_id": user_id},
        dedup_key=f"{user_id}:{name}:{user_id}-n1",
        user_id=user_id,
    )
    LinkReconcileRepository(database.engine, job_repo).mark_and_enqueue(
        user_id, name, {f"{user_id}-n1"}
    )


def workspace_table_counts(database, *, workspace: str, owner_id: str) -> dict[str, int]:
    """Row counts across every workspace-scoped table ``seed_full_workspace`` touches."""
    from sqlalchemy import func
    from sqlmodel import Session, col, select

    from kajet_turbo.models import (
        DanglingLink,
        FolderMeta,
        Job,
        LinkReconcileDirty,
        Note,
        NoteLink,
        NoteShareLink,
        NoteShareLinkVisit,
        NoteTag,
        Tag,
        WorkspaceAccess,
        WorkspaceMeta,
        WorkspaceRemote,
    )

    with Session(database.engine) as session:

        def count(model, **filters) -> int:
            stmt = select(model)
            for key, value in filters.items():
                stmt = stmt.where(getattr(model, key) == value)
            return len(session.exec(stmt).all())

        def job_count() -> int:
            return len(
                session.exec(
                    select(Job).where(
                        col(Job.user_id) == owner_id,
                        func.json_extract(col(Job.payload), "$.workspace") == workspace,
                    )
                ).all()
            )

        return {
            "notes": count(Note, workspace=workspace, owner_id=owner_id),
            "note_tags": len(
                session.exec(
                    select(NoteTag)
                    .join(Tag, col(NoteTag.tag_id) == col(Tag.id))
                    .where(Tag.workspace == workspace, Tag.owner_id == owner_id)
                ).all()
            ),
            "tags": count(Tag, workspace=workspace, owner_id=owner_id),
            "note_links": count(NoteLink, workspace=workspace, owner_id=owner_id),
            "note_share_links": count(NoteShareLink, workspace=workspace, owner_id=owner_id),
            "note_share_link_visits": len(
                session.exec(
                    select(NoteShareLinkVisit)
                    .join(NoteShareLink)
                    .where(
                        NoteShareLink.workspace == workspace,
                        NoteShareLink.owner_id == owner_id,
                    )
                ).all()
            ),
            "dangling_links": count(DanglingLink, workspace=workspace, owner_id=owner_id),
            "folder_meta": count(FolderMeta, workspace=workspace, owner_id=owner_id),
            "workspace_remote": count(WorkspaceRemote, workspace=workspace, user_id=owner_id),
            "workspace_access": count(WorkspaceAccess, workspace=workspace, user_id=owner_id),
            "workspace_meta": count(WorkspaceMeta, workspace=workspace, user_id=owner_id),
            "jobs": job_count(),
            "link_reconcile_dirty": count(
                LinkReconcileDirty, workspace=workspace, owner_id=owner_id
            ),
        }


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
