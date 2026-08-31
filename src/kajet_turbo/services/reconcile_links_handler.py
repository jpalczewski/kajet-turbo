"""Targeted background repair of persisted note-link resolution."""

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from kajet_turbo.log import logger
from kajet_turbo.markdown import LinkResolution
from kajet_turbo.models import Note
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.notes.links import NoteLinkService, WorkspaceLinks
from kajet_turbo.workspace import note_filepath, read_note_file, workspace_path

_MAX_STABILITY_PASSES = 3


@dataclass(frozen=True, slots=True)
class _SourceState:
    """File-backed source revision used to detect an overlapping mutation."""

    note: Note | None
    path: str | None
    content: str | None

    @property
    def revision(self) -> tuple[str | None, str | None, str | None, str | None]:
        if self.note is None:
            return (None, None, None, None)
        return (self.note.folder, self.note.title, self.note.updated_at, self.content)


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

    def _load_states(
        self,
        owner_id: str,
        workspace: str,
        root: str,
        source_ids: set[str],
    ) -> dict[str, _SourceState]:
        notes = {
            note.id: note
            for note in self._notes.get_many(sorted(source_ids), owner_id)
            if note.workspace == workspace
        }
        states: dict[str, _SourceState] = {}
        for source_id in source_ids:
            note = notes.get(source_id)
            path = None if note is None else note_filepath(root, note.folder, note.title)
            content = None
            if path is not None and Path(path).is_file():
                with suppress(FileNotFoundError):
                    _, content = read_note_file(path)
            states[source_id] = _SourceState(note, path, content)
        return states

    def _persist_states(
        self,
        owner_id: str,
        workspace: str,
        snapshot: WorkspaceLinks,
        states: dict[str, _SourceState],
    ) -> tuple[dict[str, LinkResolution], set[str]]:
        resolutions: dict[str, LinkResolution] = {}
        missing_sources: set[str] = set()
        for source_id, state in states.items():
            if state.note is not None and state.content is not None:
                resolutions[source_id] = snapshot.resolve(state.content, state.note.folder)
                continue
            missing_sources.add(source_id)
            logger.warning(
                "link_reconcile_source_file_missing",
                owner_id=owner_id,
                ws=workspace,
                note_id=source_id,
                path=state.path,
            )
        self._links.persist_many(
            workspace,
            owner_id,
            resolutions,
            clear_source_ids=missing_sources,
        )
        return resolutions, missing_sources

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
        root = workspace_path(workspace, self._workspaces_dir, user_id=owner_id)
        acknowledged: dict[str, int] = {}
        states = self._load_states(owner_id, workspace, root, source_ids)
        pending = set(source_ids)
        passes = 0
        while pending and passes < _MAX_STABILITY_PASSES:
            passes += 1
            current = {source_id: states[source_id] for source_id in pending}
            self._persist_states(owner_id, workspace, snapshot, current)

            # A foreground content edit writes its file and DB row before replacing its
            # link graph. If it raced with the batch above, either this re-read observes
            # the new source and we retry it, or its own graph write necessarily lands
            # after ours. The same ordering makes a concurrent deletion safe.
            verified = self._load_states(owner_id, workspace, root, pending)
            changed = {
                source_id
                for source_id in pending
                if verified[source_id].revision != current[source_id].revision
            }
            stable = pending - changed
            acknowledged.update(
                {
                    source_id: generations[source_id]
                    for source_id in stable
                    if source_id in generations
                }
            )
            states.update(verified)
            pending = changed

        if pending:
            # Avoid an unbounded loop under a hot source. Bumping the generations while
            # this job is running atomically creates one serialized follow-up job.
            self._dirty.mark_and_enqueue(owner_id, workspace, pending)
            logger.info(
                "link_reconcile_sources_requeued",
                owner_id=owner_id,
                ws=workspace,
                sources=len(pending),
            )

        self._dirty.acknowledge(owner_id, workspace, acknowledged)
        rebuilt = sum(
            state.note is not None and state.content is not None for state in states.values()
        )
        missing = len(states) - rebuilt
        logger.info(
            "links_reconciled",
            owner_id=owner_id,
            ws=workspace,
            sources=len(source_ids),
            rebuilt=rebuilt,
            missing=missing,
            passes=passes,
        )
