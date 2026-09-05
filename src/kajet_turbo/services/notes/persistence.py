"""Caller-owned note persistence primitives shared by the write and reconcile domains
(#222, part of #156/#218). Nothing here opens a ``Session``, an ``operation()``, or a
transaction, and nothing times, logs, or commits — the caller does all of that.
"""

import json
from dataclasses import dataclass

from sqlmodel import Session

from kajet_turbo.models import Note
from kajet_turbo.repositories.notes import (
    NoteChunkRepository,
    NoteLinkRepository,
    NoteRepository,
    NoteTagRepository,
)
from kajet_turbo.services.notes.links import NoteLinkService


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

    Both scopes tear down the same artifacts in the same order (tags, chunks, the note
    row, then links) — keeping them on one object is what stops the ordering from
    drifting between ``NoteService.delete``/``delete_many``/``reconcile_paths`` and
    ``clear_workspace_data`` as those callers move to separate services under #156.
    """

    tag_repo: NoteTagRepository
    chunk_repo: NoteChunkRepository
    crud_repo: NoteRepository
    link_repo: NoteLinkRepository
    link_service: NoteLinkService

    def note_in_session(self, session: Session, note: Note) -> None:
        """Remove every DB artifact of ``note`` inside the caller's transaction."""
        self.tag_repo.delete_note_tags_in_session(session, note.id, note.workspace, note.owner_id)
        self.chunk_repo.delete_chunks(note.id, session)
        self.crud_repo.delete_in_session(session, note.id, owner_id=note.owner_id)
        self.link_repo.delete_links_from_in_session(session, note.id)
        self.link_repo.delete_links_to_in_session(session, note.id)
        self.link_service.delete_dangling_for_source_in_session(session, note.id)

    def workspace_in_session(self, session: Session, ws_name: str, owner_id: str) -> None:
        """Remove every note-related row for a whole workspace inside the caller's
        transaction. FK ordering: chunks must be deleted before notes
        (``note_chunks.note_id`` FK)."""
        self.tag_repo.delete_workspace_tags_in_session(session, ws_name, owner_id)
        self.chunk_repo.delete_for_workspace_in_session(ws_name, owner_id, session)
        self.crud_repo.delete_for_workspace_in_session(ws_name, owner_id, session)
        self.link_repo.delete_workspace_links_in_session(session, ws_name, owner_id)
        self.link_service.delete_dangling_for_workspace_in_session(session, ws_name, owner_id)
