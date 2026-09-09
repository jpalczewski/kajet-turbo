"""Note editing: ``update``/``edit_many``/``restore_version`` — split off ``NoteService``
under #388.
"""

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from secrets import token_hex

from sqlmodel import Session

from kajet_turbo.log import logger
from kajet_turbo.markdown import (
    BrokenWikilinkError,
    EditSpec,
    IndexedNote,
    LinkResolution,
    apply_edit,
)
from kajet_turbo.repositories.git import GitError, GitRepository, target_write_transaction
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.indexing import Indexer
from kajet_turbo.services.notes.batch import (
    _BatchValidationError,
    _validate_destructive_items,
)
from kajet_turbo.services.notes.history import NoteVersionService
from kajet_turbo.services.notes.links import NoteLinkService, wikilink_warnings
from kajet_turbo.services.notes.locator import locate_many
from kajet_turbo.services.notes.paths import conflict_message, note_path_conflict
from kajet_turbo.services.notes.persistence import defer_index_many, defer_index_note
from kajet_turbo.services.notes.staged_change import StagedChange, commit_rows_then_tree
from kajet_turbo.services.notes.staleness import current_head_sha, sha_is_fresh, stale_payload
from kajet_turbo.services.notes.tags import NoteTagService
from kajet_turbo.services.notes.types import EditBatchItem
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget
from kajet_turbo.workspace import (
    InvalidFolderError,
    LocatedNote,
    NoteFrontmatter,
    normalize_folder,
    note_filepath,
    read_note_file_raw,
    resolve_temporal_fields,
    temporal_drop_warnings,
    write_note_file,
)


@dataclass(frozen=True, slots=True)
class _PreparedEdit:
    """A fully validated edit, ready for the atomic write phase."""

    index: int
    note_id: str
    loc: LocatedNote
    meta: NoteFrontmatter
    new_content: str
    new_tags: list[str]
    occurred_at: str | None
    period: str | None
    links: LinkResolution
    replaced: int | None
    raw: bytes


_UNCHANGED = object()
# Metadata-only edit: mode="overwrite" + content=None leaves the body untouched (see
# apply_edit). Module-level so update()'s default isn't a call in the signature (B008).
_NO_EDIT = EditSpec()


class NoteEditService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        link_service: NoteLinkService,
        tag_service: NoteTagService,
        version_service: NoteVersionService,
        indexer: Indexer | None = None,
        reconcile_repo: LinkReconcileRepository | None = None,
    ) -> None:
        self._crud_repo = crud_repo
        self._link_service = link_service
        self._tag_service = tag_service
        self._version_service = version_service
        self._indexer = indexer
        self._reconcile_repo = reconcile_repo

    @target_write_transaction
    def update(
        self,
        target: NoteTarget,
        expected_sha: str | None,
        title: str | None = None,
        tags: list[str] | None = None,
        folder: str | None = None,
        edit: EditSpec = _NO_EDIT,
        *,
        extras: dict[str, object] | None = None,
        extras_replace: bool = False,
        occurred_at: object = _UNCHANGED,
        period: object = _UNCHANGED,
        clear_date_metadata: bool = False,
    ) -> dict:
        note_id = target.note_id
        owner_id = target.workspace.owner_id
        ws_path = str(target.workspace.path)
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Note not found: note_id={note_id}")
        now = datetime.now(UTC).isoformat()
        new_title = title if title is not None else note.title
        try:
            new_folder = normalize_folder(folder) if folder is not None else note.folder
        except ValueError as e:
            raise InvalidFolderError(str(e)) from e
        current_tags = json.loads(note.tags or "[]")
        new_tags = NoteTagService.normalize_tags(tags) if tags is not None else current_tags
        new_occurred_at, new_period = resolve_temporal_fields(
            has_occurred_at=occurred_at is not _UNCHANGED,
            has_period=period is not _UNCHANGED,
            occurred_at=occurred_at,
            period=period,
            clear=clear_date_metadata,
            fallback=(note.occurred_at, note.period),
        )

        old_path = note_filepath(ws_path, note.folder, note.title)
        new_path = note_filepath(ws_path, new_folder, new_title)
        old_rel = str(Path(old_path).relative_to(ws_path))
        new_rel = str(Path(new_path).relative_to(ws_path))

        if not Path(old_path).exists():
            raise FileNotFoundError(f"Note file not found: note_id={note_id}")

        if not sha_is_fresh(current_head_sha(ws_path, old_rel), expected_sha):
            return stale_payload(note_id)

        workspace_links = self._link_service.for_workspace(note.workspace, owner_id)
        if old_path != new_path:
            conflict = note_path_conflict(
                workspace_links.paths, ws_path, new_folder, new_title, exclude_id=note_id
            )
            if conflict is not None:
                raise FileExistsError(conflict_message(new_title, new_path, conflict))
        existing_meta, old_content, old_raw = read_note_file_raw(old_path)
        if not clear_date_metadata and occurred_at is _UNCHANGED and period is _UNCHANGED:
            # The file is source of truth during a read-modify-write. This also repairs
            # a temporal value hand-edited since the last reconcile instead of overwriting
            # it — unless read_note_file had to drop it as unparseable, in which case the
            # DB's last-known-good value (already `new_occurred_at`/`new_period` from
            # resolve_temporal_fields above) is kept instead of persisting the drop.
            new_occurred_at, new_period = existing_meta.temporal_or(new_occurred_at, new_period)
        if extras is None:
            new_extras = existing_meta.extras
        elif extras_replace:
            new_extras = extras
        else:
            # Merge, not replace (#352): caller-supplied keys win, existing hand-written
            # keys not mentioned here survive — matching write_note_file's #105 behavior.
            new_extras = {**existing_meta.extras, **extras}
        # apply_edit owns every mode/parameter rule, including "overwrite without content
        # leaves the body alone" — the metadata-only edit path.
        edit_result = apply_edit(old_content, edit)
        new_content = edit_result.body
        replaced = edit_result.replaced

        # workspace_links was hoisted above (before the collision check) — this snapshot
        # serves validation and any backlink rewrite below too.
        links = workspace_links.validate(new_content, new_folder)
        identity_changed = note.title != new_title or note.folder != new_folder
        affected_sources = (
            workspace_links.affected_sources({note.title, new_title}, include_source_ids={note_id})
            if identity_changed
            else set()
        )

        apply_meta = replace(
            existing_meta,
            id=note_id,
            title=new_title,
            tags=new_tags,
            created_at=note.created_at,
            updated_at=now,
            extras=new_extras,
            occurred_at=new_occurred_at,
            period=new_period,
        )
        # Rename and content are one commit (#118): a failure rolls back to the old path
        # with byte-identical old content, never leaving the DB row pointing at a path
        # that doesn't exist. One GitRepository either way — a second open would re-read
        # refs/pack indexes for no reason.
        repo = GitRepository(ws_path)

        def apply_update() -> None:
            if old_path == new_path:
                write_note_file(new_path, apply_meta, new_content)
                return
            # Route the rename leg through a temp name in old_path's own folder — same
            # choreography as move_folder's tmp_root and NoteFolderService.move()
            # (#181) — so a case-only rename never self-collides against its own
            # not-yet-moved source, and the exists() check below is meaningful
            # regardless of the filesystem's case sensitivity. Without this, on a
            # case-insensitive-but-case-preserving filesystem (macOS APFS, Windows
            # NTFS) old_path and new_path are the same physical file: writing
            # new_content to new_path then unlinking old_path would delete it.
            tmp_path = Path(old_path).parent / f".kajet-rename-{token_hex(8)}"
            try:
                Path(old_path).rename(tmp_path)
            except OSError as e:
                raise GitError(str(e)) from e
            if Path(new_path).exists():
                tmp_path.rename(old_path)
                raise FileExistsError(f"Target file '{new_rel}' already exists.")
            try:
                write_note_file(new_path, apply_meta, new_content)
            except OSError as e:
                tmp_path.rename(old_path)
                raise GitError(str(e)) from e
            tmp_path.unlink(missing_ok=True)

        item = StagedChange(
            add=new_rel,
            remove=old_rel if old_path != new_path else None,
            apply=apply_update,
            known_bytes=old_raw,
        )

        def write_row(session: Session) -> None:
            self._crud_repo.update_in_session(
                session,
                note_id,
                owner_id=owner_id,
                title=new_title,
                tags=new_tags,
                updated_at=now,
                folder=new_folder,
                occurred_at=new_occurred_at,
                period=new_period,
                bump_index_generation=True,
            )

        commit_rows_then_tree(
            self._crud_repo,
            repo,
            [item],
            f"note: update {new_title}",
            operation="update",
            write_rows=write_row,
            note_id=note_id,
            owner_id=owner_id,
        )
        self._link_service.persist(note_id, note.workspace, owner_id, links)
        self._tag_service.sync_tags(note_id, note.workspace, owner_id, new_tags, new_content)
        if old_path != new_path:
            move = (
                IndexedNote(note_id, note.folder, note.title),
                IndexedNote(note_id, new_folder, new_title),
            )
            workspace_links.rewrite_backlinks([move], ws_path, repo)
        logger.info("note_updated", note_id=note_id, folder=new_folder)
        defer_index_note(
            self._indexer,
            ws_path,
            note_id,
            note.workspace,
            owner_id,
            new_title,
            new_content,
            note.index_generation + 1,
        )
        if self._reconcile_repo is not None and identity_changed:
            self._reconcile_repo.mark_and_enqueue(owner_id, note.workspace, affected_sources)
        return {
            "note_id": note_id,
            "replaced": replaced,
            "warnings": wikilink_warnings(links),
            "temporal_warnings": temporal_drop_warnings(existing_meta.temporal_dropped),
            "occurred_at": new_occurred_at,
            "period": new_period,
        }

    @target_write_transaction
    def edit_many(
        self,
        target: WorkspaceTarget,
        edits: list[EditBatchItem],
    ) -> dict:
        """Apply multiple surgical edits in ONE atomic commit. All-or-nothing at
        validation: any invalid edit (missing note, duplicate note_id, broken wikilink,
        bad anchor/heading) rejects the whole batch — nothing is written. Content + tags
        only; no title/folder changes (a rename needs backlink rewrites across other
        notes, incompatible with one commit_files call — use update() for that).

        `target` identifies the single authorized workspace every edit's note_id must
        resolve into -- the adapter already prevalidated that via
        TargetResolver.notes_in_one_workspace before calling this. This method still
        runs its own note-found/duplicate/sha-freshness checks below independently
        (defense in depth, not a redundant substitute for that prevalidation).
        """
        user_id = target.owner_id
        ws_name = target.name
        ws_path = str(target.path)
        if not edits:
            raise ValueError("Edit batch cannot be empty.")

        # One open repo for the whole batch: staleness checks during validation and
        # the single atomic commit afterwards, instead of re-opening per item.
        git_repo = GitRepository(ws_path)
        note_ids = [e.note_id.strip() for e in edits]
        expected_shas = [e.expected_sha for e in edits]
        located = locate_many(self._crud_repo, note_ids, user_id, ws_path, git_repo)
        workspace_links = self._link_service.for_workspace(ws_name, user_id)
        errors: list[dict] = []
        prepared: list[_PreparedEdit] = []
        for item in _validate_destructive_items(note_ids, expected_shas, located):
            if isinstance(item, _BatchValidationError):
                errors.append(item.as_dict())
                continue
            index, note_id, loc = item.index, item.note_id, item.loc
            edit_item = edits[index]
            existing_meta, old_content, raw = read_note_file_raw(loc.filepath)
            # 'overwrite' without content is edit_note's metadata-only path, but this batch
            # cannot rename or move — so with no tags either, the item has nothing left to
            # change and would commit an untouched file while reporting success. Every other
            # mode already errors on a missing payload inside apply_edit.
            if (
                edit_item.edit.mode == "overwrite"
                and edit_item.edit.content is None
                and edit_item.tags is None
                and edit_item.occurred_at is None
                and edit_item.period is None
                and not edit_item.clear_date_metadata
            ):
                errors.append(
                    {
                        "index": index,
                        "note_id": note_id,
                        "error": "Item changes nothing: it carries neither content nor tags. "
                        "Use edit_note to change title or folder.",
                    }
                )
                continue
            try:
                edit_result = apply_edit(old_content, edit_item.edit)
            except ValueError as e:
                errors.append({"index": index, "note_id": note_id, "error": str(e)})
                continue
            new_content = edit_result.body
            try:
                links = workspace_links.validate(new_content, loc.note.folder)
            except BrokenWikilinkError as e:
                errors.append({"index": index, "note_id": note_id, "error": str(e)})
                continue
            new_tags = (
                NoteTagService.normalize_tags(edit_item.tags)
                if edit_item.tags is not None
                else NoteTagService.normalize_tags(existing_meta.tags)
            )
            clear_date_metadata = edit_item.clear_date_metadata
            try:
                occurred_at, period = resolve_temporal_fields(
                    has_occurred_at=edit_item.occurred_at is not None,
                    has_period=edit_item.period is not None,
                    occurred_at=edit_item.occurred_at,
                    period=edit_item.period,
                    clear=clear_date_metadata,
                    # A field read_note_file had to drop as unparseable falls back to the
                    # DB's last-known-good value instead of the file's (now None) one, so
                    # an unrelated edit never silently persists the drop (#132 follow-up).
                    fallback=existing_meta.temporal_or(loc.note.occurred_at, loc.note.period),
                )
            except ValueError as e:
                errors.append({"index": index, "note_id": note_id, "error": str(e)})
                continue
            prepared.append(
                _PreparedEdit(
                    index=index,
                    note_id=note_id,
                    loc=loc,
                    meta=existing_meta,
                    new_content=new_content,
                    new_tags=new_tags,
                    occurred_at=occurred_at,
                    period=period,
                    links=links,
                    replaced=edit_result.replaced,
                    raw=raw,
                )
            )

        if errors:
            return {"applied": False, "errors": errors}

        now = datetime.now(UTC).isoformat()
        n = len(prepared)
        items = [
            StagedChange(
                add=p.loc.relative,
                remove=None,
                apply=partial(
                    write_note_file,
                    p.loc.filepath,
                    replace(
                        p.meta,
                        id=p.note_id,
                        title=p.loc.note.title,
                        tags=p.new_tags,
                        created_at=p.loc.note.created_at,
                        updated_at=now,
                        occurred_at=p.occurred_at,
                        period=p.period,
                    ),
                    p.new_content,
                ),
                known_bytes=p.raw,
            )
            for p in prepared
        ]
        message = f"note: edit {n} note{'' if n == 1 else 's'}"

        def write_rows(session: Session) -> None:
            for p in prepared:
                self._crud_repo.update_in_session(
                    session,
                    p.note_id,
                    owner_id=user_id,
                    title=p.loc.note.title,
                    tags=p.new_tags,
                    updated_at=now,
                    folder=p.loc.note.folder,
                    occurred_at=p.occurred_at,
                    period=p.period,
                    bump_index_generation=True,
                )

        commit_rows_then_tree(
            self._crud_repo,
            git_repo,
            items,
            message,
            operation="update_many",
            write_rows=write_rows,
            workspace=ws_name,
            owner_id=user_id,
            count=n,
        )
        self._link_service.persist_many(
            ws_name,
            user_id,
            {p.note_id: p.links for p in prepared},
        )
        for p in prepared:
            self._tag_service.sync_tags(p.note_id, ws_name, user_id, p.new_tags, p.new_content)

        defer_index_many(self._indexer, ws_path, ws_name, user_id, [p.note_id for p in prepared])

        results = [
            {
                "index": p.index,
                "note_id": p.note_id,
                "replaced": p.replaced,
                "warnings": wikilink_warnings(p.links),
                "temporal_warnings": temporal_drop_warnings(p.meta.temporal_dropped),
            }
            for p in prepared
        ]
        for p in prepared:
            logger.info("note_updated", note_id=p.note_id, folder=p.loc.note.folder)
        logger.info("notes_edited_batch", ws=ws_name, count=len(prepared))
        return {"applied": True, "results": results}

    @target_write_transaction
    def restore_version(
        self,
        target: NoteTarget,
        sha: str,
        expected_sha: str | None = None,
    ) -> dict:
        """Restore a past version over HEAD. expected_sha (MCP callers) proves the
        caller saw the HEAD it is about to overwrite; ``None`` (REST API) skips it."""
        note_id = target.note_id
        owner_id = target.workspace.owner_id
        ws_path = str(target.workspace.path)
        version = self._version_service.get_version(target, sha)
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Note not found: note_id={note_id}")
        relative = str(Path(note_filepath(ws_path, note.folder, note.title)).relative_to(ws_path))
        current_sha = current_head_sha(ws_path, relative)
        if current_sha is None:
            raise ValueError(f"Note has no commit history: note_id={note_id}")
        if expected_sha is not None and not sha_is_fresh(current_sha, expected_sha):
            return stale_payload(note_id)
        # Restore proves intent by construction: update()'s own staleness check is
        # satisfied with the head sha just read.
        return self.update(
            target,
            expected_sha=current_sha,
            edit=EditSpec(content=version.content),
            tags=version.tags,
            extras=version.extras,
            extras_replace=True,
            occurred_at=version.occurred_at,
            period=version.period,
        )
