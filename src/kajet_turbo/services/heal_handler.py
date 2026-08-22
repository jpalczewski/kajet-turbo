"""Reverse-heal job handler: reconcile a workspace's dangling links against current
notes. For each dangling row whose target now resolves and whose source note still
exists, insert the note_links edge and delete the row. Orphan rows (source note gone)
are also deleted. Idempotent: a re-run is a no-op."""

from kajet_turbo.log import logger
from kajet_turbo.markdown import LinkIndex, join_target
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.notes import NoteLinkRepository, NoteRepository


class HealDanglingHandler:
    """Reconciles a workspace's dangling links against current notes. For each dangling
    row whose stored target now resolves (same Obsidian-style suffix rules as save-time
    validation, ranked from the source note's folder) and whose source note still exists,
    inserts the note_links edge and deletes the row. Orphan rows (source gone) are deleted.
    Idempotent: a re-run finds nothing left. Reads no note files — pure DB reconciliation."""

    def __init__(
        self,
        note_repo: NoteRepository,
        link_repo: NoteLinkRepository,
        dangling_repo: DanglingLinkRepository,
    ):
        self._notes = note_repo
        self._links = link_repo
        self._dangling = dangling_repo

    def __call__(self, payload: dict) -> None:
        user_id = payload["user_id"]
        workspace = payload["workspace"]
        rows = self._dangling.list_for_workspace(user_id, workspace)
        if not rows:
            return
        # One narrow query serves both roles: the resolution index and the source lookup
        # (dangling rows are workspace-scoped, so their sources are all in this snapshot).
        paths = self._notes.list_paths(workspace, user_id)
        index = LinkIndex(paths)
        sources = {p.note_id: p for p in paths}
        healed = 0
        for r in rows:
            source = sources.get(r["source_note_id"])
            if source is None:
                self._dangling.delete(r["id"])  # orphan: source note gone, clean regardless
                continue
            target = join_target(r["target_folder"], r["target_title"])
            hit = index.resolve(target, source.folder)
            if hit is None:
                continue  # target still missing — leave the row
            self._links.add_link(r["source_note_id"], hit.note_id, workspace, user_id)
            self._dangling.delete(r["id"])
            healed += 1
        if healed:
            logger.info("dangling_healed", ws=workspace, count=healed)
