"""Workspace-cleanup and disk/DB reconciliation domain, split off the write pipeline
under #156/#218 so deleting a workspace or repairing its index no longer requires
constructing the full note write service (#225, #226).
"""

from kajet_turbo.log import logger
from kajet_turbo.repositories.notes import (
    NoteChunkRepository,
    NoteLinkRepository,
    NoteRepository,
    NoteTagRepository,
)
from kajet_turbo.services.notes.links import NoteLinkService
from kajet_turbo.services.notes.persistence import NoteTeardown


class NoteReconcileService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        link_repo: NoteLinkRepository,
        tag_repo: NoteTagRepository,
        chunk_repo: NoteChunkRepository,
        link_service: NoteLinkService,
    ) -> None:
        self._crud_repo = crud_repo
        self._teardown = NoteTeardown(tag_repo, chunk_repo, crud_repo, link_repo, link_service)

    def clear_workspace_data(self, ws_name: str, owner_id: str) -> None:
        """Delete every note-related row for a workspace: tags, chunks (+ FTS/vec),
        notes, and links. Used by workspace deletion. NOT used by reconcile/reindex
        (see reconcile_paths) — a wipe-then-rebuild has no window where the deletion
        safety valve could measure anything, and a crash mid-run loses every row."""
        with self._crud_repo.operation(
            "clear_workspace_data", workspace=ws_name, owner_id=owner_id
        ) as operation:
            session = operation.session
            with session.begin():
                self._teardown.workspace_in_session(session, ws_name, owner_id)
        logger.info("workspace_data_cleared", ws=ws_name, owner_id=owner_id)
