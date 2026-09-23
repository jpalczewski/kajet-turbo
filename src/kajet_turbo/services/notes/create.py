"""Note creation: ``save``/``save_many`` — split off ``NoteService`` under #388.

Both entry points build a note through the same steps (#451): ``_NoteDraft.build``
normalizes and validates the input, ``_NoteDraft.ensure_path_free`` checks the target
path, and
``NoteCreateService._write`` commits rows + tree and does the link/tag/reconcile
bookkeeping. They differ only in how a failure surfaces (``save`` raises, ``save_many``
reports a ``BatchNoteError`` per item), how the batch shares one link index, and how the
result is indexed and returned.
"""

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path

from nanoid import generate
from sqlmodel import Session

from kajet_turbo.log import logger
from kajet_turbo.markdown import BrokenWikilinkError, IndexedNote, LinkResolution
from kajet_turbo.models import Note
from kajet_turbo.repositories.git import GitRepository, target_write_transaction
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.indexing import Indexer
from kajet_turbo.services.notes.links import NoteLinkService, WorkspaceLinks, wikilink_warnings
from kajet_turbo.services.notes.paths import (
    build_path_index,
    conflict_message,
    note_path_conflict,
    path_conflict_key,
)
from kajet_turbo.services.notes.persistence import defer_index_many, defer_index_note, new_note_row
from kajet_turbo.services.notes.staged_change import StagedChange, commit_rows_then_tree
from kajet_turbo.services.notes.tags import NoteTagService
from kajet_turbo.services.notes.types import (
    BatchNoteError,
    BatchNoteSuccess,
    NewNote,
    SavedNoteResult,
)
from kajet_turbo.services.targets import WorkspaceTarget
from kajet_turbo.workspace import (
    ExtrasReservedKeyError,
    InvalidFolderError,
    NoteFrontmatter,
    TemporalMetadataError,
    normalize_folder,
    normalize_temporal_metadata,
    note_filepath,
    write_note_file,
)


@dataclass(frozen=True, slots=True)
class _NoteDraft:
    """A ``NewNote`` with normalized fields, an assigned id, and its computed path —
    nothing validated against the workspace yet, nothing written."""

    note_id: str
    title: str
    content: str
    tags: list[str]
    folder: str
    occurred_at: str | None
    period: str | None
    created_at: str
    meta: NoteFrontmatter
    filepath: str
    relative: str

    @classmethod
    def build(cls, note: NewNote, ws_path: str, now: str) -> _NoteDraft:
        """Normalize and validate ``note`` — every per-note input rule lives here, so a
        batch can report it per item. Raises ``InvalidFolderError``,
        ``TemporalMetadataError`` or ``ExtrasReservedKeyError``; performs no workspace
        lookup."""
        try:
            folder = normalize_folder(note.folder)
        except ValueError as e:
            raise InvalidFolderError(str(e)) from e
        occurred_at, period = normalize_temporal_metadata(note.occurred_at, note.period)
        note_id = generate(size=7)
        tags = NoteTagService.normalize_tags(note.tags)
        # Built here, not at write time: NoteFrontmatter is what rejects an ``extras`` key
        # shadowing a reserved one, and that must fail this item, not the whole batch.
        meta = NoteFrontmatter(
            id=note_id,
            title=note.title,
            tags=tags,
            created_at=now,
            updated_at=now,
            occurred_at=occurred_at,
            period=period,
            extras=note.extras or {},
        )
        filepath = note_filepath(ws_path, folder, note.title)
        return cls(
            note_id=note_id,
            title=note.title,
            content=note.content,
            tags=tags,
            folder=folder,
            occurred_at=occurred_at,
            period=period,
            created_at=now,
            meta=meta,
            filepath=filepath,
            relative=str(Path(filepath).relative_to(ws_path)),
        )

    @property
    def path_key(self) -> str:
        return path_conflict_key(self.filepath)

    @property
    def indexed(self) -> IndexedNote:
        return IndexedNote(self.note_id, self.folder, self.title)

    def ensure_path_free(self, conflict: IndexedNote | None) -> None:
        """Raise ``FileExistsError`` if ``conflict`` (the row the caller found claiming
        this path — a one-off scan for ``save``, a shared index for ``save_many``) or an
        orphan file on disk already claims it."""
        if conflict is not None:
            raise FileExistsError(conflict_message(self.title, self.filepath, conflict))
        if Path(self.filepath).exists():
            raise FileExistsError(f"File '{Path(self.filepath).name}' already exists on disk.")

    def staged_change(self) -> StagedChange:
        return StagedChange(
            add=self.relative,
            remove=None,
            apply=partial(write_note_file, self.filepath, self.meta, self.content),
        )

    def row(self, workspace: str, owner_id: str) -> Note:
        return new_note_row(
            note_id=self.note_id,
            workspace=workspace,
            owner_id=owner_id,
            title=self.title,
            folder=self.folder,
            tags=self.tags,
            created_at=self.created_at,
            updated_at=self.created_at,
            occurred_at=self.occurred_at,
            period=self.period,
        )


@dataclass(frozen=True, slots=True)
class _PreparedNote:
    """A draft whose path is claimed and whose wikilinks resolved — ready to write."""

    draft: _NoteDraft
    links: LinkResolution


class _BatchItemRejected(ValueError):
    """A batch-only rule (blank title, duplicate in batch) failed for one item."""


# Everything a single batch item can legitimately fail with. Anything else raised while
# admitting an item is a bug and propagates instead of being reported as bad input.
_ITEM_REJECTIONS = (
    _BatchItemRejected,
    InvalidFolderError,
    TemporalMetadataError,
    ExtrasReservedKeyError,
    FileExistsError,
)


def _admit_batch_item(
    note: NewNote,
    ws_path: str,
    now: str,
    accepted: set[tuple[str, str]],
    path_index: dict[str, IndexedNote],
) -> _NoteDraft:
    """``save_many``'s Phase 1 for one item: the shared draft + path checks, plus the two
    batch-only rules — a title that is blank once stripped, and a (folder, title) pair an
    earlier item already took. Raises one of ``_ITEM_REJECTIONS``; the caller registers
    an admitted draft in ``accepted``/``path_index``."""
    title = note.title.strip()
    if not title:
        raise _BatchItemRejected("Title is required.")
    draft = _NoteDraft.build(replace(note, title=title), ws_path, now)
    if (draft.folder, title) in accepted:
        raise _BatchItemRejected(
            f"Duplicate in batch: '{title}' in folder '{draft.folder or 'root'}'."
        )
    draft.ensure_path_free(path_index.get(draft.path_key))
    return draft


class NoteCreateService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        link_service: NoteLinkService,
        tag_service: NoteTagService,
        indexer: Indexer | None = None,
        reconcile_repo: LinkReconcileRepository | None = None,
    ) -> None:
        self._crud_repo = crud_repo
        self._link_service = link_service
        self._tag_service = tag_service
        self._indexer = indexer
        self._reconcile_repo = reconcile_repo

    @target_write_transaction
    def save(
        self,
        target: WorkspaceTarget,
        title: str,
        content: str,
        tags: list[str],
        folder: str = "",
        occurred_at: date | str | None = None,
        period: str | None = None,
        extras: dict[str, object] | None = None,
    ) -> SavedNoteResult:
        ws_path = str(target.path)
        draft = _NoteDraft.build(
            NewNote(
                title=title,
                content=content,
                tags=tags,
                folder=folder,
                occurred_at=occurred_at,
                period=period,
                extras=extras,
            ),
            ws_path,
            datetime.now(UTC).isoformat(),
        )
        workspace_links = self._link_service.for_workspace(target.name, target.owner_id)
        draft.ensure_path_free(
            note_path_conflict(workspace_links.paths, ws_path, draft.folder, draft.title)
        )
        prepared = _PreparedNote(draft, workspace_links.validate(content, draft.folder))

        self._write(
            target,
            [prepared],
            workspace_links,
            message=f"note: add {title}",
            operation="insert",
            note_id=draft.note_id,
        )
        defer_index_note(
            self._indexer, ws_path, draft.note_id, target.name, target.owner_id, title, content, 1
        )
        return SavedNoteResult(
            note_id=draft.note_id,
            warnings=wikilink_warnings(prepared.links),
            occurred_at=draft.occurred_at,
            period=draft.period,
        )

    @target_write_transaction
    def save_many(
        self,
        target: WorkspaceTarget,
        notes: list[NewNote],
    ) -> list[BatchNoteSuccess | BatchNoteError]:
        """Create many notes in one batch: one DB transaction, one git commit, one cache
        bump, embeddings parallelized across the indexer threadpool. Best-effort per
        note — invalid notes are reported and skipped.
        Returns per-note ``{index, note_id}`` | ``{index, error}``, input order preserved.
        Raises GitError or OSError if a write or the batch commit fails (every file
        actually written is rolled back first).
        """
        ws_path = str(target.path)
        results: list[BatchNoteSuccess | BatchNoteError | None] = [None] * len(notes)
        now = datetime.now(UTC).isoformat()

        # Phase 1: normalization, uniqueness, id assignment. Survivors join the batch's
        # link index so in-batch wikilinks resolve in Phase 2. `base_links` is a snapshot
        # of the workspace's DB rows, taken once up front under the workspace write lock
        # (Phase 2 extends it via with_extra instead of re-querying). `path_index` maps
        # each already-claimed path (existing rows, then batch items as they're accepted)
        # to its note, so every item's conflict check is one O(1) lookup.
        base_links = self._link_service.for_workspace(target.name, target.owner_id)
        path_index = build_path_index(base_links.paths, ws_path)
        accepted: set[tuple[str, str]] = set()
        drafts: list[tuple[int, _NoteDraft]] = []
        for index, note in enumerate(notes):
            try:
                draft = _admit_batch_item(note, ws_path, now, accepted, path_index)
            except _ITEM_REJECTIONS as e:
                results[index] = BatchNoteError(index=index, error=str(e))
                continue
            accepted.add((draft.folder, draft.title))
            path_index[draft.path_key] = draft.indexed
            drafts.append((index, draft))

        # Phase 2: wikilink resolution against existing notes union the batch, sharing one
        # index. Non-cascading: the index is not rebuilt as notes are dropped, so a link to
        # a later-dropped note still resolves (worst case a harmless orphan edge).
        workspace_links = base_links.with_extra(d.indexed for _, d in drafts)
        valid: list[tuple[int, _PreparedNote]] = []
        for index, draft in drafts:
            try:
                links = workspace_links.validate(draft.content, draft.folder)
            except BrokenWikilinkError as e:
                results[index] = BatchNoteError(index=index, error=str(e))
                continue
            valid.append((index, _PreparedNote(draft, links)))

        if valid:
            n = len(valid)
            prepared = [p for _, p in valid]
            self._write(
                target,
                prepared,
                workspace_links,
                message=f"note: add {n} note{'' if n == 1 else 's'}",
                operation="insert_many",
                count=n,
            )
            # Only enqueues a reindex_note job per note (see NoteIndexer.index_many) —
            # chunking/FTS/embeddings run later in the background, not before this returns.
            defer_index_many(
                self._indexer,
                ws_path,
                target.name,
                target.owner_id,
                [p.draft.note_id for p in prepared],
            )
            for index, p in valid:
                results[index] = BatchNoteSuccess(
                    index=index, note_id=p.draft.note_id, warnings=wikilink_warnings(p.links)
                )

        return [r for r in results if r is not None]

    def _write(
        self,
        target: WorkspaceTarget,
        prepared: list[_PreparedNote],
        workspace_links: WorkspaceLinks,
        *,
        message: str,
        operation: str,
        **operation_fields: object,
    ) -> None:
        """Persist ``prepared`` — rows first, tree last, in one transaction (#155;
        ``commit_rows_then_tree`` rolls both back on either failure) — then its link
        graph and tags, and queue link reconciliation for notes in ``workspace_links``
        whose dangling links the new titles may now resolve. Indexing stays with the
        caller: ``save`` indexes inline, ``save_many`` only enqueues jobs."""
        ws_name, owner_id = target.name, target.owner_id
        affected_sources = workspace_links.affected_sources({p.draft.title for p in prepared})

        def write_rows(session: Session) -> None:
            for p in prepared:
                self._crud_repo.insert_in_session(session, p.draft.row(ws_name, owner_id))

        commit_rows_then_tree(
            self._crud_repo,
            GitRepository(str(target.path)),
            [p.draft.staged_change() for p in prepared],
            message,
            operation=operation,
            write_rows=write_rows,
            workspace=ws_name,
            owner_id=owner_id,
            **operation_fields,
        )
        self._link_service.persist_many(
            ws_name, owner_id, {p.draft.note_id: p.links for p in prepared}
        )
        for p in prepared:
            self._tag_service.sync_tags(
                p.draft.note_id, ws_name, owner_id, p.draft.tags, p.draft.content
            )
            logger.info("note_saved", note_id=p.draft.note_id, ws=ws_name, folder=p.draft.folder)
        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(owner_id, ws_name, affected_sources)
