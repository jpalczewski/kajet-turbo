"""Caller-owned note persistence primitives shared by the write and reconcile domains
(#222, part of #156/#218). Nothing here opens a ``Session``, an ``operation()``, or a
transaction, and nothing times, logs, or commits — the caller does all of that.
"""

import json
from dataclasses import dataclass
from functools import partial

from sqlmodel import Session

from kajet_turbo.models import Note
from kajet_turbo.repositories.git import defer_workspace_postprocess
from kajet_turbo.repositories.note_share_link import NoteShareLinkRepository
from kajet_turbo.repositories.notes import (
    NoteChunkRepository,
    NoteLinkRepository,
    NoteRepository,
    NoteTagRepository,
)
from kajet_turbo.services.indexing import Indexer
from kajet_turbo.services.notes.links import NoteLinkService


def defer_index_note(
    indexer: Indexer | None,
    ws_path: str,
    note_id: str,
    workspace: str,
    owner_id: str,
    title: str,
    content: str,
    expected_generation: int,
) -> None:
    """Defer a single-note (re)index past the workspace write lock. Chunks + FTS are the
    reliable search backbone (a real DB write error surfaces); the embedding HTTP
    roundtrip is deferred further still, to an embed_note job index_note itself enqueues.
    Shared by ``save()`` and ``update()`` — see ``NoteIndexer.index_note``."""
    if indexer is None:
        return
    defer_workspace_postprocess(
        ws_path,
        partial(
            indexer.index_note,
            note_id,
            workspace,
            owner_id,
            title,
            content,
            expected_generation=expected_generation,
        ),
    )


def defer_index_many(
    indexer: Indexer | None,
    ws_path: str,
    workspace: str,
    owner_id: str,
    note_ids: list[str],
) -> None:
    """Defer a batch reindex past the workspace write lock. Shared by ``save_many()``,
    ``edit_many()``, and ``reconcile_paths()`` — see ``NoteIndexer.index_many``."""
    if indexer is None:
        return
    defer_workspace_postprocess(ws_path, partial(indexer.index_many, workspace, owner_id, note_ids))


def new_note_row(
    *,
    note_id: str,
    workspace: str,
    owner_id: str,
    title: str,
    folder: str,
    tags: list[str],
    created_at: str,
    updated_at: str,
    occurred_at: str | None,
    period: str | None,
) -> Note:
    """Build a ``Note`` row for ``insert_in_session`` — the shape shared by ``save()``,
    ``save_many()``, and ``reconcile_paths()``'s adoption path."""
    return Note(
        id=note_id,
        workspace=workspace,
        owner_id=owner_id,
        title=title,
        folder=folder,
        tags=json.dumps(tags),
        created_at=created_at,
        updated_at=updated_at,
        occurred_at=occurred_at,
        period=period,
    )


@dataclass(frozen=True, slots=True)
class NoteTeardown:
    """FK-sensitive note deletion, note- and workspace-scoped, in one implementation.

    Both scopes tear down the same artifacts in the same order (tags, chunks, share
    links, the note row, then links) — keeping them on one object is what stops the
    ordering from drifting between ``NoteDeleteService.delete``/``delete_many``,
    ``NoteReconcileService.reconcile_paths``, and ``clear_workspace_data``. Built once in
    ``dependencies.py`` and injected into both ``NoteDeleteService`` and
    ``NoteReconcileService`` (#388) — the same instance, not two independently constructed
    ones, so the sharing this docstring describes is enforced by construction, not by
    convention.
    """

    tag_repo: NoteTagRepository
    chunk_repo: NoteChunkRepository
    crud_repo: NoteRepository
    link_repo: NoteLinkRepository
    link_service: NoteLinkService
    share_link_repo: NoteShareLinkRepository

    def note_in_session(self, session: Session, note: Note) -> None:
        """Remove every DB artifact of ``note`` inside the caller's transaction."""
        self.tag_repo.delete_note_tags_in_session(session, note.id, note.workspace, note.owner_id)
        self.chunk_repo.delete_chunks(note.id, session)
        self.share_link_repo.delete_visits_for_note_in_session(session, note.id)
        self.share_link_repo.delete_for_note_in_session(session, note.id)
        self.crud_repo.delete_in_session(session, note.id, owner_id=note.owner_id)
        self.link_repo.delete_links_from_in_session(session, note.id)
        self.link_repo.delete_links_to_in_session(session, note.id)
        self.link_service.delete_dangling_for_source_in_session(session, note.id)

    def workspace_in_session(self, session: Session, ws_name: str, owner_id: str) -> None:
        """Remove every note-related row for a whole workspace inside the caller's
        transaction. FK ordering: chunks and share links must be deleted before notes
        (``note_chunks.note_id``/``note_share_links.note_id`` FKs)."""
        self.tag_repo.delete_workspace_tags_in_session(session, ws_name, owner_id)
        self.chunk_repo.delete_for_workspace_in_session(ws_name, owner_id, session)
        self.share_link_repo.delete_visits_for_workspace_in_session(session, ws_name, owner_id)
        self.share_link_repo.delete_for_workspace_in_session(session, ws_name, owner_id)
        self.crud_repo.delete_for_workspace_in_session(ws_name, owner_id, session)
        self.link_repo.delete_workspace_links_in_session(session, ws_name, owner_id)
        self.link_service.delete_dangling_for_workspace_in_session(session, ws_name, owner_id)
