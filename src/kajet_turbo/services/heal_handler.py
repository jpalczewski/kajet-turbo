"""Reverse-heal job handler: reconcile a workspace's dangling links against current
notes. For each dangling row whose (target_folder, target_title) now resolves and
whose source note still exists, insert the note_links edge and delete the row.
Orphan rows (source note gone) are also deleted. Idempotent: a re-run is a no-op."""

from kajet_turbo.log import logger
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.notes import NoteRepository


class HealDanglingHandler:
    """Reconciles a workspace's dangling links against current notes. For each dangling
    row whose (folder, title) now resolves and whose source note still exists, inserts the
    note_links edge and deletes the row. Orphan rows (source gone) are deleted. Idempotent:
    a re-run finds nothing left. Reads no note files — pure DB reconciliation."""

    def __init__(self, note_repo: NoteRepository, dangling_repo: DanglingLinkRepository):
        self._notes = note_repo
        self._dangling = dangling_repo

    def __call__(self, payload: dict) -> None:
        user_id = payload["user_id"]
        workspace = payload["workspace"]
        rows = self._dangling.list_for_workspace(user_id, workspace)
        if not rows:
            return
        pairs = list({(r["target_folder"], r["target_title"]) for r in rows})
        resolved = self._notes.resolve_paths(workspace, user_id, pairs)
        healed = 0
        for r in rows:
            target_id = resolved.get((r["target_folder"], r["target_title"]))
            if target_id is None:
                continue  # target still missing — leave the row
            if self._notes.get(r["source_note_id"], owner_id=user_id) is None:
                self._dangling.delete(r["id"])  # orphan: source note gone
                continue
            self._notes.add_link(r["source_note_id"], target_id, workspace, user_id)
            self._dangling.delete(r["id"])
            healed += 1
        if healed:
            logger.info("dangling_healed", ws=workspace, count=healed)
