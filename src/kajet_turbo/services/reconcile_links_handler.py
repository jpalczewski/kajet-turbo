"""Targeted background repair of persisted note-link resolution."""

from pathlib import Path

from kajet_turbo.log import logger
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.notes.links import NoteLinkService
from kajet_turbo.workspace import note_filepath, read_note_file, workspace_path


class ReconcileLinksHandler:
    """Rebuild only dirty sources against one current workspace link snapshot.

    The handler never writes Git files or search rows. Replacing graph and dangling
    rows is idempotent. Generation-aware acknowledgement leaves a newer concurrent
    marker intact, guaranteeing a follow-up job can repair any stale intermediate result.
    """

    def __init__(
        self,
        note_repo: NoteRepository,
        link_service: NoteLinkService,
        dangling_repo: DanglingLinkRepository,
        dirty_repo: LinkReconcileRepository,
        workspaces_dir: str,
    ) -> None:
        self._notes = note_repo
        self._links = link_service
        self._dangling = dangling_repo
        self._dirty = dirty_repo
        self._workspaces_dir = workspaces_dir

    def __call__(self, payload: dict) -> None:
        owner_id = payload["user_id"]
        workspace = payload["workspace"]
        generations = self._dirty.list_dirty(owner_id, workspace)
        source_ids = set(generations)

        # Jobs written by the old post-commit heal hook have no mode. Rebuild their
        # dangling sources with the new handler so an in-place deploy drains them safely.
        if payload.get("mode") != "targeted":
            source_ids.update(self._dangling.source_ids_for_workspace(owner_id, workspace))
        if not source_ids:
            return

        snapshot = self._links.for_workspace(workspace, owner_id)
        notes = {
            note.id: note
            for note in self._notes.get_many(sorted(source_ids), owner_id)
            if note.workspace == workspace
        }
        root = workspace_path(workspace, self._workspaces_dir, user_id=owner_id)
        acknowledged: dict[str, int] = {}
        resolutions = {}
        missing_sources: set[str] = set()
        for source_id in sorted(source_ids):
            note = notes.get(source_id)
            path = None if note is None else note_filepath(root, note.folder, note.title)
            if note is not None and path is not None and Path(path).is_file():
                try:
                    content = read_note_file(path)["content"]
                except FileNotFoundError:
                    pass  # disappeared after is_file(); handle as missing below
                else:
                    resolutions[source_id] = snapshot.resolve(content, note.folder)
                    if source_id in generations:
                        acknowledged[source_id] = generations[source_id]
                    continue
            missing_sources.add(source_id)
            logger.warning(
                "link_reconcile_source_file_missing",
                owner_id=owner_id,
                ws=workspace,
                note_id=source_id,
                path=path,
            )
            if source_id in generations:
                acknowledged[source_id] = generations[source_id]

        self._links.persist_many(
            workspace,
            owner_id,
            resolutions,
            clear_source_ids=missing_sources,
        )
        self._dirty.acknowledge(owner_id, workspace, acknowledged)
        logger.info(
            "links_reconciled",
            owner_id=owner_id,
            ws=workspace,
            sources=len(source_ids),
            rebuilt=len(resolutions),
            missing=len(missing_sources),
        )
