"""Note creation: ``save``/``save_many`` — split off ``NoteService`` under #388."""

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from nanoid import generate
from sqlmodel import Session

from kajet_turbo.log import logger
from kajet_turbo.markdown import BrokenWikilinkError, IndexedNote, LinkResolution
from kajet_turbo.repositories.git import GitRepository, target_write_transaction
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.indexing import Indexer
from kajet_turbo.services.notes.links import NoteLinkService, wikilink_warnings
from kajet_turbo.services.notes.paths import (
    build_path_index,
    conflict_message,
    note_path_conflict,
    path_conflict_key,
)
from kajet_turbo.services.notes.persistence import defer_index_many, defer_index_note, new_note_row
from kajet_turbo.services.notes.staged_change import StagedChange, commit_rows_then_tree
from kajet_turbo.services.notes.tags import NoteTagService
from kajet_turbo.services.targets import WorkspaceTarget
from kajet_turbo.workspace import (
    NoteFrontmatter,
    normalize_folder,
    normalize_temporal_metadata,
    note_filepath,
    write_note_file,
)


@dataclass(frozen=True, slots=True)
class _SaveCandidate:
    """One save_many item that passed Phase 1 (uniqueness + id assignment), not yet
    validated for wikilinks."""

    index: int
    note_id: str
    title: str
    content: str
    tags: list[str]
    folder: str
    filepath: str
    relative: str
    occurred_at: str | None
    period: str | None


@dataclass(frozen=True, slots=True)
class _PreparedSave:
    """A fully validated save_many item, ready for the atomic write phase."""

    candidate: _SaveCandidate
    links: LinkResolution


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
        defer_index_note(self._indexer, ws_path, note_id, ws_name, user_id, title, content, 1)
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
        candidates: list[_SaveCandidate] = []
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
            try:
                candidate_occurred_at, candidate_period = normalize_temporal_metadata(
                    raw.get("occurred_at"), raw.get("period")
                )
            except ValueError as e:
                results[index] = {"index": index, "error": str(e)}
                continue
            accepted.add(key)
            new_note = IndexedNote(note_id, folder, title)
            batch_notes.append(new_note)
            path_index[path_conflict_key(filepath)] = new_note
            candidates.append(
                _SaveCandidate(
                    index=index,
                    note_id=note_id,
                    title=title,
                    content=str(raw.get("content", "")),
                    tags=NoteTagService.normalize_tags(raw.get("tags", []) or []),
                    folder=folder,
                    filepath=filepath,
                    relative=relative,
                    occurred_at=candidate_occurred_at,
                    period=candidate_period,
                )
            )

        # Phase 2: wikilink resolution against existing notes union the batch, sharing one
        # index. Non-cascading: the index is not rebuilt as notes are dropped, so a link to
        # a later-dropped note still resolves (worst case a harmless orphan edge).
        valid: list[_PreparedSave] = []
        workspace_links = base_links.with_extra(batch_notes)
        for c in candidates:
            try:
                links = workspace_links.validate(c.content, c.folder)
            except BrokenWikilinkError as e:
                results[c.index] = {"index": c.index, "error": str(e)}
                continue
            valid.append(_PreparedSave(candidate=c, links=links))

        if not valid:
            return [r for r in results if r is not None]

        affected_sources = workspace_links.affected_sources({p.candidate.title for p in valid})

        # Phase 3: rows first, tree last, one transaction (#155) — commit_rows_then_tree
        # rolls back the batch on either a DB or a git failure.
        n = len(valid)
        items = [
            StagedChange(
                add=p.candidate.relative,
                remove=None,
                apply=partial(
                    write_note_file,
                    p.candidate.filepath,
                    NoteFrontmatter(
                        id=p.candidate.note_id,
                        title=p.candidate.title,
                        tags=p.candidate.tags,
                        created_at=now,
                        updated_at=now,
                        occurred_at=p.candidate.occurred_at,
                        period=p.candidate.period,
                    ),
                    p.candidate.content,
                ),
            )
            for p in valid
        ]

        def write_rows(session: Session) -> None:
            for p in valid:
                self._crud_repo.insert_in_session(
                    session,
                    new_note_row(
                        note_id=p.candidate.note_id,
                        workspace=ws_name,
                        owner_id=user_id,
                        title=p.candidate.title,
                        folder=p.candidate.folder,
                        tags=p.candidate.tags,
                        created_at=now,
                        updated_at=now,
                        occurred_at=p.candidate.occurred_at,
                        period=p.candidate.period,
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
            {p.candidate.note_id: p.links for p in valid},
        )
        for p in valid:
            self._tag_service.sync_tags(
                p.candidate.note_id, ws_name, user_id, p.candidate.tags, p.candidate.content
            )

        # Phase 5: index after releasing the workspace write lock. This only enqueues a
        # reindex_note job per note (see NoteIndexer.index_many) — chunking/FTS/embeddings
        # run later in the background, not before this call returns.
        defer_index_many(
            self._indexer, ws_path, ws_name, user_id, [p.candidate.note_id for p in valid]
        )

        for p in valid:
            results[p.candidate.index] = {
                "index": p.candidate.index,
                "note_id": p.candidate.note_id,
                "warnings": wikilink_warnings(p.links),
            }
            logger.info(
                "note_saved", note_id=p.candidate.note_id, ws=ws_name, folder=p.candidate.folder
            )

        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(user_id, ws_name, affected_sources)

        return [r for r in results if r is not None]
