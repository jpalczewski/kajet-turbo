"""Note deletion: ``delete``/``delete_many`` — split off ``NoteService`` under #388."""

from pathlib import Path

from kajet_turbo import perf
from kajet_turbo.log import logger
from kajet_turbo.repositories.git import GitRepository, target_write_transaction
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.notes import NoteRepository, NoteTagRepository
from kajet_turbo.services.notes.batch import (
    _BatchValidationError,
    _validate_destructive_items,
    _ValidatedDestructiveItem,
)
from kajet_turbo.services.notes.links import NoteLinkService
from kajet_turbo.services.notes.locator import locate_many
from kajet_turbo.services.notes.persistence import NoteTeardown
from kajet_turbo.services.notes.staleness import current_head_sha, sha_is_fresh, stale_payload
from kajet_turbo.services.notes.types import DeleteBatchItem
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget
from kajet_turbo.workspace import note_filepath


class NoteDeleteService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        tag_repo: NoteTagRepository,
        link_service: NoteLinkService,
        teardown: NoteTeardown,
        reconcile_repo: LinkReconcileRepository | None = None,
    ) -> None:
        self._crud_repo = crud_repo
        self._tag_repo = tag_repo
        self._link_service = link_service
        self._teardown = teardown
        self._reconcile_repo = reconcile_repo

    @target_write_transaction
    def delete(self, target: NoteTarget, expected_sha: str | None = None) -> dict:
        """Delete a note. expected_sha (MCP callers) must match the note's HEAD
        commit; ``None`` (REST API) skips the check. A missing file (orphaned DB
        row) also skips it — there is no version the caller could have read, and
        the delete is then pure index cleanup.

        Rows are torn down first, inside the DB transaction; the Git commit that
        removes the file runs last, inside that same transaction, and the transaction
        commits only after both steps succeed. A teardown failure leaves the file
        untouched. A Git failure rolls back the row teardown. This is not two-phase
        commit: a DB commit failure after the Git commit has already landed leaves the
        file gone with the row intact (see #155 for the general fix).

        Cost: the app's one shared SQLite write lock is now held for the Git commit too,
        not just the row teardown — see ``delete_many``'s docstring for the batch-sized
        version of this trade-off.
        """
        note_id = target.note_id
        owner_id = target.workspace.owner_id
        ws_path = str(target.workspace.path)
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Note not found: note_id={note_id}")
        filepath = note_filepath(ws_path, note.folder, note.title)
        file_exists = Path(filepath).exists()
        relative = ""
        if file_exists:
            relative = str(Path(filepath).relative_to(ws_path))
            if expected_sha is not None and not sha_is_fresh(
                current_head_sha(ws_path, relative), expected_sha
            ):
                return stale_payload(note_id)
        workspace_links = self._link_service.for_workspace(note.workspace, owner_id)
        affected_sources = workspace_links.affected_sources({note.title})
        affected_sources.discard(note_id)  # this source is synchronously deleted below
        with (
            self._crud_repo.operation(
                "delete", note_id=note_id, workspace=note.workspace, owner_id=owner_id
            ) as operation,
            operation.session.begin(),
        ):
            self._teardown.note_in_session(operation.session, note)
            self._tag_repo.sweep_orphan_tags_in_session(
                operation.session, note.workspace, note.owner_id
            )
            if file_exists:
                # perf: keep this commit's wall time out of db_ms, see perf.excluded_from.
                with perf.excluded_from("db_ms"):
                    GitRepository(ws_path).delete_file(relative, f"note: delete {note_id}")
        logger.info("note_deleted", note_id=note_id)
        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(owner_id, note.workspace, affected_sources)
        return {"note_id": note_id}

    @target_write_transaction
    def delete_many(
        self,
        target: WorkspaceTarget,
        deletes: list[DeleteBatchItem],
    ) -> dict:
        """Delete multiple notes in one Git commit and one DB transaction.

        All-or-nothing at validation: an invalid item (missing note, duplicate note_id, stale
        expected_sha) rejects the whole batch — nothing is deleted. ``expected_sha`` is the
        current HEAD commit sha (from get_history), proving the caller has seen the version
        it is about to destroy; a mismatch rejects the item without revealing the current
        sha, forcing a real re-read instead of a blind retry.

        Rows are torn down first, inside the DB transaction; the single batched Git commit
        that removes the files runs last, inside that same transaction, and the transaction
        commits only after both steps succeed. A teardown failure leaves the files untouched.
        A Git failure rolls back the row teardown. This is not two-phase commit: a DB commit
        failure after the Git commit has already landed leaves the files gone with the rows
        intact (see #155 for the general fix).

        Cost: the app's one shared SQLite write lock — normally held only for the row
        teardown — is now held for the batched Git commit too, and that commit first waits
        (up to ``KAJET_GIT_LOCK_TIMEOUT``, 10s default) on this workspace's own write lock if
        another commit is in flight. A slow or contended batch here can make an unrelated
        write elsewhere in the app hit SQLite's ``busy_timeout`` (5s) and fail with
        "database is locked". Accepted for this batch size today; #155 tracks the general
        shape (this trade-off applies to every write path it touches, not just deletes).
        """
        user_id = target.owner_id
        ws_name = target.name
        ws_path = str(target.path)
        if not deletes:
            raise ValueError("Delete batch cannot be empty.")

        # One open repo for the whole batch: staleness checks during validation and
        # the single atomic commit afterwards, instead of re-opening per item.
        git_repo = GitRepository(ws_path)
        note_ids = [d.note_id.strip() for d in deletes]
        expected_shas = [d.expected_sha for d in deletes]
        located = locate_many(self._crud_repo, note_ids, user_id, ws_path, git_repo)
        errors: list[dict] = []
        prepared: list[_ValidatedDestructiveItem] = []
        for item in _validate_destructive_items(note_ids, expected_shas, located):
            if isinstance(item, _BatchValidationError):
                errors.append(item.as_dict())
            else:
                prepared.append(item)

        if errors:
            return {"applied": False, "errors": errors}

        workspace_links = self._link_service.for_workspace(ws_name, user_id)
        affected_sources = workspace_links.affected_sources({p.loc.note.title for p in prepared})
        affected_sources.difference_update(p.note_id for p in prepared)

        n = len(prepared)
        with (
            self._crud_repo.operation(
                "delete_many", workspace=ws_name, owner_id=user_id, count=len(prepared)
            ) as operation,
            operation.session.begin(),
        ):
            for p in prepared:
                self._teardown.note_in_session(operation.session, p.loc.note)
            self._tag_repo.sweep_orphan_tags_in_session(operation.session, ws_name, user_id)
            # perf: keep this commit's wall time out of db_ms, see perf.excluded_from.
            with perf.excluded_from("db_ms"):
                git_repo.delete_files(
                    [p.loc.relative for p in prepared],
                    f"note: delete {n} note{'' if n == 1 else 's'}",
                )
        for p in prepared:
            logger.info("note_deleted", note_id=p.note_id)

        logger.info("notes_deleted_batch", ws=ws_name, count=len(prepared))
        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(user_id, ws_name, affected_sources)
        results = [{"index": p.index, "note_id": p.note_id} for p in prepared]
        return {"applied": True, "results": results}
