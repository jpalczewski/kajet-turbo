import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from secrets import token_hex

from nanoid import generate
from sqlmodel import Session

from kajet_turbo import perf
from kajet_turbo.log import logger
from kajet_turbo.markdown import (
    BrokenWikilinkError,
    EditSpec,
    IndexedNote,
    LinkResolution,
    apply_edit,
)
from kajet_turbo.repositories.git import (
    GitError,
    GitRepository,
    defer_workspace_postprocess,
    target_write_transaction,
)
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.note_share_link import NoteShareLinkRepository
from kajet_turbo.repositories.notes import (
    NoteChunkRepository,
    NoteLinkRepository,
    NoteRepository,
    NoteTagRepository,
)
from kajet_turbo.services.notes.history import NoteVersionService
from kajet_turbo.services.notes.links import NoteLinkService, wikilink_warnings
from kajet_turbo.services.notes.locator import locate_many
from kajet_turbo.services.notes.paths import (
    build_path_index,
    conflict_message,
    note_path_conflict,
    path_conflict_key,
)
from kajet_turbo.services.notes.persistence import NoteTeardown, new_note_row
from kajet_turbo.services.notes.staged_change import (
    StagedChange,
    commit_rows_then_tree,
)
from kajet_turbo.services.notes.staleness import (
    current_head_sha,
    sha_is_fresh,
    stale_error,
    stale_payload,
)
from kajet_turbo.services.notes.tags import NoteTagService
from kajet_turbo.services.notes.types import EditBatchItem
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget
from kajet_turbo.workspace import (
    InvalidFolderError,
    LocatedNote,
    NoteFrontmatter,
    normalize_folder,
    normalize_temporal_metadata,
    note_filepath,
    read_note_file_raw,
    resolve_temporal_fields,
    temporal_drop_warnings,
    write_note_file,
)


@dataclass(frozen=True, slots=True)
class _ValidatedDestructiveItem:
    """A batch item that passed the validation shared by edits and deletes.

    Carries only what the shared check produces — the caller recovers its own richer
    per-item data (edit payload, tags, ...) via ``index`` into its original input list."""

    index: int
    note_id: str
    loc: LocatedNote


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


@dataclass(frozen=True, slots=True)
class _RenamedNote:
    """One note staged for a tag rename: what to write, and what body changed for chunking."""

    loc: LocatedNote
    meta: NoteFrontmatter
    new_tags: list[str]
    new_body: str
    old_body: str

    @property
    def note(self):
        return self.loc.note

    @property
    def body_changed(self) -> bool:
        """True when the rename reached inline ``#hashtags``, so the chunks are stale."""
        return self.new_body != self.old_body


@dataclass(frozen=True, slots=True)
class _BatchValidationError:
    """A public-shaped validation error, kept typed while flowing through a batch."""

    index: int
    note_id: str
    error: str

    def as_dict(self) -> dict:
        return {"index": self.index, "note_id": self.note_id, "error": self.error}


_UNCHANGED = object()
# Metadata-only edit: mode="overwrite" + content=None leaves the body untouched (see
# apply_edit). Module-level so update()'s default isn't a call in the signature (B008).
_NO_EDIT = EditSpec()


class NoteService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        link_repo: NoteLinkRepository,
        tag_repo: NoteTagRepository,
        chunk_repo: NoteChunkRepository,
        tag_service: NoteTagService,
        link_service: NoteLinkService,
        version_service: NoteVersionService,
        share_link_repo: NoteShareLinkRepository,
        indexer=None,
        reconcile_repo: LinkReconcileRepository | None = None,
    ) -> None:
        self._crud_repo = crud_repo
        self._link_repo = link_repo
        self._tag_repo = tag_repo
        self._chunk_repo = chunk_repo
        self._tag_service = tag_service
        self._link_service = link_service
        self._version_service = version_service
        self._share_link_repo = share_link_repo
        self._indexer = indexer
        self._reconcile_repo = reconcile_repo
        self._teardown = NoteTeardown(
            tag_repo, chunk_repo, crud_repo, link_repo, link_service, share_link_repo
        )

    def _validate_destructive_items(
        self,
        note_ids: list[str],
        expected_shas: list[str],
        located: dict[str, LocatedNote],
    ) -> Iterator[_ValidatedDestructiveItem | _BatchValidationError]:
        """Yield shared validation results in input order for batch writes.

        Keeping this as a stream lets edit_many add its edit-specific validation
        immediately, preserving the existing order of mixed validation errors.
        """
        seen_ids: set[str] = set()
        for index, (note_id, expected_sha) in enumerate(zip(note_ids, expected_shas, strict=True)):
            if not note_id:
                yield _BatchValidationError(index, note_id, "note_id is required.")
                continue
            if note_id in seen_ids:
                yield _BatchValidationError(
                    index, note_id, f"Duplicate note_id in batch: '{note_id}'."
                )
                continue
            seen_ids.add(note_id)
            loc = located.get(note_id)
            if loc is None:
                yield _BatchValidationError(index, note_id, f"Note not found: note_id={note_id}")
                continue
            if not loc.file_exists:
                yield _BatchValidationError(
                    index, note_id, f"Note file not found: note_id={note_id}"
                )
                continue
            stripped_sha = expected_sha.strip()
            if not stripped_sha:
                yield _BatchValidationError(index, note_id, "expected_sha is required.")
                continue
            if not sha_is_fresh(loc.head_sha, stripped_sha):
                yield _BatchValidationError(index, note_id, stale_error(note_id))
                continue
            yield _ValidatedDestructiveItem(index, note_id, loc)

    def _index(
        self,
        note_id: str,
        ws_name: str,
        owner_id: str,
        title: str,
        content: str,
        expected_generation: int,
    ) -> None:
        # Chunks + FTS are the reliable search backbone (written by replace_chunks inside
        # index_note); a real DB write error surfaces. The embedding HTTP roundtrip is
        # deferred to an embed_note job — index_note only enqueues, never hits the network.
        if self._indexer is None:
            return
        self._indexer.index_note(
            note_id,
            ws_name,
            owner_id,
            title,
            content,
            expected_generation=expected_generation,
        )

    @target_write_transaction
    def save(
        self,
        target: WorkspaceTarget,
        title: str,
        content: str,
        tags: list[str],
        folder: str = "",
        occurred_at: object = None,
        period: object = None,
        extras: dict[str, object] | None = None,
    ) -> dict:
        user_id = target.owner_id
        ws_name = target.name
        ws_path = str(target.path)
        folder = normalize_folder(folder)
        occurred_at, period = normalize_temporal_metadata(occurred_at, period)
        tags = NoteTagService.normalize_tags(tags)
        workspace_links = self._link_service.for_workspace(ws_name, user_id)
        filepath = note_filepath(ws_path, folder, title)
        relative = str(Path(filepath).relative_to(ws_path))
        conflict = note_path_conflict(workspace_links.paths, ws_path, folder, title)
        if conflict is not None:
            raise FileExistsError(conflict_message(title, filepath, conflict))
        if Path(filepath).exists():
            raise FileExistsError(f"File '{Path(filepath).name}' already exists on disk.")
        links = workspace_links.validate(content, folder)
        affected_sources = workspace_links.affected_sources({title})
        note_id = generate(size=7)
        now = datetime.now(UTC).isoformat()
        meta = NoteFrontmatter(
            id=note_id,
            title=title,
            tags=tags,
            created_at=now,
            updated_at=now,
            occurred_at=occurred_at,
            period=period,
            extras=extras or {},
        )
        item = StagedChange(
            add=relative, remove=None, apply=partial(write_note_file, filepath, meta, content)
        )

        def write_row(session: Session) -> None:
            self._crud_repo.insert_in_session(
                session,
                new_note_row(
                    note_id=note_id,
                    workspace=ws_name,
                    owner_id=user_id,
                    title=title,
                    folder=folder,
                    tags=tags,
                    created_at=now,
                    updated_at=now,
                    occurred_at=occurred_at,
                    period=period,
                ),
            )

        commit_rows_then_tree(
            self._crud_repo,
            GitRepository(ws_path),
            [item],
            f"note: add {title}",
            operation="insert",
            write_rows=write_row,
            note_id=note_id,
            workspace=ws_name,
            owner_id=user_id,
        )
        self._link_service.persist(note_id, ws_name, user_id, links)
        self._tag_service.sync_tags(note_id, ws_name, user_id, tags, content)
        logger.info("note_saved", note_id=note_id, ws=ws_name, folder=folder)
        defer_workspace_postprocess(
            ws_path, partial(self._index, note_id, ws_name, user_id, title, content, 1)
        )
        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(user_id, ws_name, affected_sources)
        return {
            "note_id": note_id,
            "warnings": wikilink_warnings(links),
            "occurred_at": occurred_at,
            "period": period,
        }

    @target_write_transaction
    def save_many(
        self,
        target: WorkspaceTarget,
        notes: list[dict],
    ) -> list[dict]:
        """Create many notes in one batch: one DB transaction, one git commit, one cache
        bump, embeddings parallelized across the indexer threadpool. Best-effort per
        note — invalid notes are reported and skipped. Each input dict:
        ``{title, content, tags=[], folder=""}``.
        Returns per-note ``{index, note_id}`` | ``{index, error}``, input order preserved.
        Raises GitError or OSError if a write or the batch commit fails (every file
        actually written is rolled back first).
        """
        user_id = target.owner_id
        ws_name = target.name
        ws_path = str(target.path)
        results: list[dict | None] = [None] * len(notes)
        now = datetime.now(UTC).isoformat()

        # Phase 1: uniqueness + id assignment. Survivors get an id and join the batch's
        # link index so in-batch wikilinks resolve in Phase 2. `base_links` is a snapshot
        # of the workspace's DB rows, taken once up front under the workspace write lock
        # (Phase 2 extends it via with_extra instead of re-querying). `path_index` maps
        # each already-claimed path (existing rows, then batch items as they're accepted)
        # to its note for an O(1) conflict check per item, instead of an O(len(notes))
        # rescan via note_path_conflict on every one of the (potentially many) items.
        base_links = self._link_service.for_workspace(ws_name, user_id)
        path_index = build_path_index(base_links.paths, ws_path)
        accepted: set[tuple[str, str]] = set()
        batch_notes: list[IndexedNote] = []
        survivors: list[dict] = []
        for index, raw in enumerate(notes):
            title = str(raw.get("title", "")).strip()
            if not title:
                results[index] = {"index": index, "error": "Title is required."}
                continue
            folder = normalize_folder(str(raw.get("folder", "")))
            key = (folder, title)
            if key in accepted:
                results[index] = {
                    "index": index,
                    "error": f"Duplicate in batch: '{title}' in folder '{folder or 'root'}'.",
                }
                continue
            filepath = note_filepath(ws_path, folder, title)
            conflict = path_index.get(path_conflict_key(filepath))
            if conflict is not None:
                results[index] = {
                    "index": index,
                    "error": conflict_message(title, filepath, conflict),
                }
                continue
            note_id = generate(size=7)
            relative = str(Path(filepath).relative_to(ws_path))
            if Path(filepath).exists():
                results[index] = {
                    "index": index,
                    "error": f"File '{Path(filepath).name}' already exists on disk.",
                }
                continue
            accepted.add(key)
            new_note = IndexedNote(note_id, folder, title)
            batch_notes.append(new_note)
            path_index[path_conflict_key(filepath)] = new_note
            survivors.append(
                {
                    "index": index,
                    "note_id": note_id,
                    "title": title,
                    "content": str(raw.get("content", "")),
                    "tags": NoteTagService.normalize_tags(raw.get("tags", []) or []),
                    "folder": folder,
                    "filepath": filepath,
                    "relative": relative,
                    "occurred_at": None,
                    "period": None,
                }
            )
            try:
                survivors[-1]["occurred_at"], survivors[-1]["period"] = normalize_temporal_metadata(
                    raw.get("occurred_at"), raw.get("period")
                )
            except ValueError as e:
                results[index] = {"index": index, "error": str(e)}
                survivors.pop()
                accepted.remove(key)
                batch_notes.pop()
                del path_index[path_conflict_key(filepath)]

        # Phase 2: wikilink resolution against existing notes union the batch, sharing one
        # index. Non-cascading: the index is not rebuilt as notes are dropped, so a link to
        # a later-dropped note still resolves (worst case a harmless orphan edge).
        valid: list[dict] = []
        workspace_links = base_links.with_extra(batch_notes)
        for s in survivors:
            try:
                s["links"] = workspace_links.validate(s["content"], s["folder"])
            except BrokenWikilinkError as e:
                results[s["index"]] = {"index": s["index"], "error": str(e)}
                continue
            valid.append(s)

        if not valid:
            return [r for r in results if r is not None]

        affected_sources = workspace_links.affected_sources({str(s["title"]) for s in valid})

        # Phase 3: rows first, tree last, one transaction (#155) — commit_rows_then_tree
        # rolls back the batch on either a DB or a git failure.
        n = len(valid)
        items = [
            StagedChange(
                add=s["relative"],
                remove=None,
                apply=partial(
                    write_note_file,
                    s["filepath"],
                    NoteFrontmatter(
                        id=s["note_id"],
                        title=s["title"],
                        tags=s["tags"],
                        created_at=now,
                        updated_at=now,
                        occurred_at=s["occurred_at"],
                        period=s["period"],
                    ),
                    s["content"],
                ),
            )
            for s in valid
        ]

        def write_rows(session: Session) -> None:
            for s in valid:
                self._crud_repo.insert_in_session(
                    session,
                    new_note_row(
                        note_id=s["note_id"],
                        workspace=ws_name,
                        owner_id=user_id,
                        title=s["title"],
                        folder=s["folder"],
                        tags=s["tags"],
                        created_at=now,
                        updated_at=now,
                        occurred_at=s["occurred_at"],
                        period=s["period"],
                    ),
                )

        commit_rows_then_tree(
            self._crud_repo,
            GitRepository(ws_path),
            items,
            f"note: add {n} note{'' if n == 1 else 's'}",
            operation="insert_many",
            write_rows=write_rows,
            workspace=ws_name,
            owner_id=user_id,
            count=n,
        )

        # Phase 4: link graph + tags.
        self._link_service.persist_many(
            ws_name,
            user_id,
            {str(s["note_id"]): s["links"] for s in valid},
        )
        for s in valid:
            self._tag_service.sync_tags(s["note_id"], ws_name, user_id, s["tags"], s["content"])

        # Phase 5: index after releasing the workspace write lock. This only enqueues a
        # reindex_note job per note (see NoteIndexer.index_many) — chunking/FTS/embeddings
        # run later in the background, not before this call returns.
        if self._indexer is not None:
            index_payload = [{"id": s["note_id"]} for s in valid]
            defer_workspace_postprocess(
                ws_path, partial(self._indexer.index_many, ws_name, user_id, index_payload)
            )

        for s in valid:
            results[s["index"]] = {
                "index": s["index"],
                "note_id": s["note_id"],
                "warnings": wikilink_warnings(s["links"]),
            }
            logger.info("note_saved", note_id=s["note_id"], ws=ws_name, folder=s["folder"])

        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(user_id, ws_name, affected_sources)

        return [r for r in results if r is not None]

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
            # it — unless read_note_file had to drop it as unparseable, in which case
            # `new_occurred_at`/`new_period` (already the DB's last-known-good value, from
            # resolve_temporal_fields above) are kept instead of persisting the drop.
            if "occurred_at" not in existing_meta.temporal_dropped:
                new_occurred_at = existing_meta.occurred_at
            if "period" not in existing_meta.temporal_dropped:
                new_period = existing_meta.period
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
        defer_workspace_postprocess(
            ws_path,
            partial(
                self._index,
                note_id,
                note.workspace,
                owner_id,
                new_title,
                new_content,
                note.index_generation + 1,
            ),
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
        for item in self._validate_destructive_items(note_ids, expected_shas, located):
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
                    fallback=(
                        existing_meta.occurred_at
                        if "occurred_at" not in existing_meta.temporal_dropped
                        else loc.note.occurred_at,
                        existing_meta.period
                        if "period" not in existing_meta.temporal_dropped
                        else loc.note.period,
                    ),
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

        if self._indexer is not None:
            index_payload = [{"id": p.note_id} for p in prepared]
            defer_workspace_postprocess(
                ws_path, partial(self._indexer.index_many, ws_name, user_id, index_payload)
            )

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
        deletes: list[dict],
    ) -> dict:
        """Delete multiple notes in one Git commit and one DB transaction.

        All-or-nothing at validation: an invalid item (missing note, duplicate note_id, stale
        expected_sha) rejects the whole batch — nothing is deleted. Each input dict has
        ``note_id`` and ``expected_sha``. The latter is the current HEAD commit sha (from
        get_history), proving the caller has seen the version it is about to destroy; a
        mismatch rejects the item without revealing the current sha, forcing a real re-read
        instead of a blind retry.

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
        note_ids = [str(raw.get("note_id", "")).strip() for raw in deletes]
        expected_shas = [str(raw.get("expected_sha", "")) for raw in deletes]
        located = locate_many(self._crud_repo, note_ids, user_id, ws_path, git_repo)
        errors: list[dict] = []
        prepared: list[_ValidatedDestructiveItem] = []
        for item in self._validate_destructive_items(note_ids, expected_shas, located):
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
