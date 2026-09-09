"""Workspace-cleanup and disk/DB reconciliation domain, split off the write pipeline
under #156/#218 so deleting a workspace or repairing its index no longer requires
constructing the full note write service (#225, #226).
"""

import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import cast

from nanoid import generate

from kajet_turbo.log import logger
from kajet_turbo.repositories.git import GitRepository, workspace_write_transaction
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.notes import NoteRepository, NoteTagRepository
from kajet_turbo.services.indexing import Indexer
from kajet_turbo.services.notes.links import NoteLinkService
from kajet_turbo.services.notes.persistence import NoteTeardown, defer_index_many, new_note_row
from kajet_turbo.services.notes.staged_change import StagedChange, staged_workspace_change
from kajet_turbo.services.notes.tags import NoteTagService
from kajet_turbo.workspace import (
    NoteFrontmatter,
    iter_note_paths,
    note_filepath,
    note_folder,
    read_note_file,
    write_note_file,
)


@dataclass(frozen=True, slots=True)
class _PresentFile:
    """One on-disk file successfully parsed during a reconcile scan."""

    note_id: str
    title: str
    tags: list[str]
    created_at: str
    updated_at: str
    content: str
    folder: str
    relative: str
    # The parsed frontmatter, kept whole (not just occurred_at/period/temporal_dropped)
    # so temporal_or() is available at the drift-check/write-back call sites below.
    meta: NoteFrontmatter

    @property
    def occurred_at(self) -> str | None:
        return self.meta.occurred_at

    @property
    def period(self) -> str | None:
        return self.meta.period


def _present_file(
    note_id: str, meta: NoteFrontmatter, content: str, folder: str, relative: str
) -> _PresentFile:
    return _PresentFile(
        note_id=note_id,
        title=meta.title or "",
        tags=NoteTagService.normalize_tags(cast(list[str], meta.tags or [])),
        created_at=str(meta.created_at or ""),
        updated_at=str(meta.updated_at or ""),
        content=content,
        folder=folder,
        relative=relative,
        meta=meta,
    )


@dataclass(frozen=True, slots=True)
class _AdoptionCandidate:
    """An on-disk file with no ``id`` in frontmatter, found during a reconcile scan."""

    relative: str
    filepath: Path
    meta: NoteFrontmatter
    content: str


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """Outcome of reconciling DB rows against a set of workspace paths."""

    inserted: list[str]
    updated: list[str]
    removed: list[str]
    unchanged: int
    duplicate_ids: list[str]
    unreadable_paths: list[str]
    adopted: list[str]

    @property
    def present(self) -> int:
        return len(self.inserted) + len(self.updated) + self.unchanged


# A reconcile refuses to execute a deletion this large rather than risk emptying the
# workspace from a path-computation bug — see reconcile_paths. Small workspaces losing a
# handful of notes to a legitimate cleanup never trip this (the floor guards that).
_RECONCILE_MAX_DELETE_RATIO = 0.2
_RECONCILE_MIN_DELETE_FLOOR = 5


class NoteReconcileService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        tag_repo: NoteTagRepository,
        link_service: NoteLinkService,
        teardown: NoteTeardown,
        indexer: Indexer | None = None,
        reconcile_repo: LinkReconcileRepository | None = None,
    ) -> None:
        self._crud_repo = crud_repo
        self._tag_repo = tag_repo
        self._link_service = link_service
        self._teardown = teardown
        self._indexer = indexer
        self._reconcile_repo = reconcile_repo

    def clear_workspace_data(self, ws_name: str, owner_id: str) -> None:
        """Delete every note-related row for a workspace: tags, chunks (+ FTS/vec),
        share links, notes, and links. Used by workspace deletion. NOT used by reconcile/reindex
        (see reconcile_paths) — a wipe-then-rebuild has no window where the deletion
        safety valve could measure anything, and a crash mid-run loses every row."""
        with self._crud_repo.operation(
            "clear_workspace_data", workspace=ws_name, owner_id=owner_id
        ) as operation:
            session = operation.session
            with session.begin():
                self._teardown.workspace_in_session(session, ws_name, owner_id)
        logger.info("workspace_data_cleared", ws=ws_name, owner_id=owner_id)

    @workspace_write_transaction
    def reconcile_paths(
        self, ws_name: str, owner_id: str, ws_path: str, paths: Iterable[str]
    ) -> ReconcileReport:
        """Re-derive DB rows from disk for exactly the given workspace-relative paths.

        A missing file (in scope, no id found there) removes its row via
        ``NoteTeardown.note_in_session``; a new id inserts one; drifted
        folder/title/tags/created_at updates it. No wipe:
        reconciling every path in scope IS the rebuild, which is what makes the deletion
        safety valve below meaningful (there's nothing to measure a wipe's blast radius
        against).

        Explicit contract decisions (issue #107 asks these be written down, not just coded):
        - Chunks are re-derived UNCONDITIONALLY for every present note. ``notes`` has no
          content column, so there is nothing to diff content against, and
          ``replace_chunks`` is idempotent — re-deriving is cheap-correct rather than a
          partial-repair guess. Chunk re-derivation is not what "updated" reports; that's
          folder/title/tags/created_at drift only.
        - Indexing never aborts the reconcile: it only enqueues ``reindex_note`` jobs now
          (``NoteIndexer.index_many``), and that enqueue is whole-batch best-effort — a
          failure logs once and is swallowed for the batch, it does not raise here. The
          actual chunk/FTS write happens later, per note, in the background job.
        - A file that fails to parse is left alone entirely — logged, never routed into
          deletion, since "unreadable" is not evidence the note is gone.
        - Two files sharing an id: the first (sorted path order) is reconciled; the rest are
          reported in ``duplicate_ids`` and left untouched — never last-write-wins.
        - A file with no ``id`` is adopted, not skipped: a fresh id is generated and written
          back into its frontmatter (every other key preserved via
          ``NoteFrontmatter.extras``), then it is treated like any newly-present note. All
          adoptions in one run share a single git commit.
        """
        start = time.monotonic()
        root = Path(ws_path)
        scoped_paths = set(paths)

        present: dict[str, _PresentFile] = {}
        duplicate_ids: list[str] = []
        unreadable_paths: list[str] = []
        adoption_candidates: list[_AdoptionCandidate] = []
        for relative in sorted(scoped_paths):
            filepath = root / relative
            if not filepath.exists():
                continue
            try:
                meta, content = read_note_file(str(filepath))
            except Exception as e:
                unreadable_paths.append(relative)
                logger.opt(exception=e).warning(
                    "reconcile_unreadable_file", ws=ws_name, path=relative
                )
                continue
            if not meta.id:
                adoption_candidates.append(
                    _AdoptionCandidate(
                        relative=relative, filepath=filepath, meta=meta, content=content
                    )
                )
                continue
            if meta.id in present:
                duplicate_ids.append(meta.id)
                logger.warning(
                    "reconcile_duplicate_note_id",
                    ws=ws_name,
                    note_id=meta.id,
                    path=relative,
                    kept_path=present[meta.id].relative,
                )
                continue
            present[meta.id] = _present_file(
                meta.id, meta, content, note_folder(ws_path, filepath), relative
            )

        adopted_ids: list[str] = []
        if adoption_candidates:
            items: list[StagedChange] = []
            pending: list[tuple[str, _AdoptionCandidate]] = []
            for candidate in adoption_candidates:
                new_id = generate(size=7)
                new_meta = replace(candidate.meta, id=new_id)
                items.append(
                    StagedChange(
                        add=candidate.relative,
                        remove=None,
                        apply=partial(
                            write_note_file, str(candidate.filepath), new_meta, candidate.content
                        ),
                    )
                )
                pending.append((new_id, candidate))
            n = len(items)
            with staged_workspace_change(
                GitRepository(ws_path), items, f"note: adopt {n} file{'' if n == 1 else 's'}"
            ):
                pass
            for new_id, candidate in pending:
                present[new_id] = _present_file(
                    new_id,
                    candidate.meta,
                    candidate.content,
                    note_folder(ws_path, candidate.filepath),
                    candidate.relative,
                )
                adopted_ids.append(new_id)
                logger.info(
                    "reconcile_note_adopted", ws=ws_name, note_id=new_id, path=candidate.relative
                )

        present_ids = set(present)
        # A path that failed to parse is not evidence its row is gone — drop it from the
        # missing-detection scope entirely, so an unreadable file can never look deleted.
        missing_scope = scoped_paths - set(unreadable_paths)
        indexed = self._crud_repo.list_paths(ws_name, owner_id)
        scoped_row_ids = {
            n.note_id
            for n in indexed
            if str(Path(note_filepath(ws_path, n.folder, n.title)).relative_to(ws_path))
            in missing_scope
        }
        missing_ids = scoped_row_ids - present_ids

        total = len(indexed)
        if missing_ids and len(missing_ids) >= _RECONCILE_MIN_DELETE_FLOOR:
            ratio = len(missing_ids) / total if total else 1.0
            if ratio > _RECONCILE_MAX_DELETE_RATIO:
                raise ValueError(
                    f"Reconcile in workspace '{ws_name}' would delete {len(missing_ids)} of "
                    f"{total} notes ({ratio:.0%}), above the "
                    f"{_RECONCILE_MAX_DELETE_RATIO:.0%} safety threshold. Refusing — check "
                    "the workspace path and disk mount before retrying."
                )

        missing_notes = self._crud_repo.get_many(list(missing_ids), owner_id) if missing_ids else []
        existing_by_id = (
            {n.id: n for n in self._crud_repo.get_many(list(present_ids), owner_id)}
            if present_ids
            else {}
        )

        inserted: list[str] = []
        updated: list[str] = []
        unchanged = 0
        # Only identity changes (folder/title) can move which source resolves to which
        # target — tags/created_at/updated_at drift alone never changes link resolution,
        # so it must not requeue every backlink (matches edit_note's identity_changed gate).
        changed_titles: set[str] = {n.title for n in missing_notes}
        for note_id, pf in present.items():
            existing = existing_by_id.get(note_id)
            if existing is None:
                inserted.append(note_id)
                changed_titles.add(pf.title)
                continue
            identity_changed = existing.folder != pf.folder or existing.title != pf.title
            pf_occurred_at, pf_period = pf.meta.temporal_or(existing.occurred_at, existing.period)
            drifted = (
                identity_changed
                or json.loads(existing.tags or "[]") != pf.tags
                or existing.created_at != pf.created_at
                or existing.updated_at != pf.updated_at
                or existing.occurred_at != pf_occurred_at
                or existing.period != pf_period
            )
            if drifted:
                updated.append(note_id)
                if identity_changed:
                    changed_titles.add(existing.title)
                    changed_titles.add(pf.title)
            else:
                unchanged += 1

        # Computed against the PRE-mutation graph, same ordering as delete()/save(): a
        # removed note's own row (and its backlinks) must still be resolvable here, or
        # target_ids_for_titles finds nothing and sources pointing at it never heal.
        affected = self._link_service.for_workspace(ws_name, owner_id).affected_sources(
            changed_titles
        )
        affected -= present_ids  # every present note gets a fresh resolution below anyway
        # The removed notes themselves are torn down synchronously above — same reason
        # delete()/delete_many() discard their own note_id from affected_sources before
        # enqueuing, rather than leaving a dirty marker for a row that no longer exists.
        affected -= missing_ids

        with (
            self._crud_repo.operation(
                "reconcile_paths",
                workspace=ws_name,
                owner_id=owner_id,
                present=len(present),
                missing=len(missing_ids),
            ) as operation,
            operation.session.begin(),
        ):
            session = operation.session
            for note in missing_notes:
                self._teardown.note_in_session(session, note)
            if missing_notes:
                self._tag_repo.sweep_orphan_tags_in_session(session, ws_name, owner_id)
            for note_id in inserted:
                pf = present[note_id]
                self._crud_repo.insert_in_session(
                    session,
                    new_note_row(
                        note_id=note_id,
                        workspace=ws_name,
                        owner_id=owner_id,
                        title=pf.title,
                        folder=pf.folder,
                        tags=pf.tags,
                        created_at=pf.created_at,
                        updated_at=pf.updated_at,
                        occurred_at=pf.occurred_at,
                        period=pf.period,
                    ),
                )
            for note_id in updated:
                pf = present[note_id]
                existing = existing_by_id[note_id]
                pf_occurred_at, pf_period = pf.meta.temporal_or(
                    existing.occurred_at, existing.period
                )
                self._crud_repo.update_in_session(
                    session,
                    note_id,
                    owner_id=owner_id,
                    title=pf.title,
                    tags=pf.tags,
                    updated_at=pf.updated_at,
                    folder=pf.folder,
                    created_at=pf.created_at,
                    occurred_at=pf_occurred_at,
                    period=pf_period,
                    bump_index_generation=True,
                )

        # Tags/links/chunks: batched, own commits — same shape as save()/_apply_tag_change,
        # never folded into the note-row transaction above.
        workspace_links = self._link_service.for_workspace(ws_name, owner_id)
        resolutions = {}
        tagged_by_note = {}
        for note_id, pf in present.items():
            tagged_by_note[note_id] = NoteTagService.tagged(pf.tags, pf.content)
            resolutions[note_id] = workspace_links.resolve(pf.content, pf.folder)
        self._tag_repo.sync_note_tags_many(ws_name, owner_id, tagged_by_note)
        self._link_service.persist_many(ws_name, owner_id, resolutions)

        # Deferred past lock release, like save()'s indexing call. index_many only
        # enqueues a reindex_note job per note now — it no longer chunks inline — but
        # deferral still keeps job-queue I/O for a whole workspace off the git lock.
        defer_index_many(self._indexer, ws_path, ws_name, owner_id, list(present))

        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(owner_id, ws_name, affected)

        logger.info(
            "reconcile_complete",
            ws=ws_name,
            inserted=len(inserted),
            updated=len(updated),
            removed=len(missing_ids),
            unchanged=unchanged,
            duplicates=len(duplicate_ids),
            unreadable=len(unreadable_paths),
            adopted=len(adopted_ids),
            duration_ms=round((time.monotonic() - start) * 1000),
        )
        return ReconcileReport(
            inserted=inserted,
            updated=updated,
            removed=sorted(missing_ids),
            unchanged=unchanged,
            duplicate_ids=duplicate_ids,
            unreadable_paths=unreadable_paths,
            adopted=adopted_ids,
        )

    def reindex(self, ws_name: str, owner_id: str, ws_path: str) -> dict:
        """Full-workspace repair: reconcile every path that exists on disk, plus every
        path a DB row currently claims — the union, so a row whose computed path never
        matched any file on disk (stale sanitization, a prior bug's residue) is still
        caught and repaired, not just files that happen to exist right now."""
        disk_paths = set(iter_note_paths(ws_path))
        indexed = self._crud_repo.list_paths(ws_name, owner_id)
        db_paths = {
            str(Path(note_filepath(ws_path, n.folder, n.title)).relative_to(ws_path))
            for n in indexed
        }
        report = self.reconcile_paths(ws_name, owner_id, ws_path, disk_paths | db_paths)
        adopted_clause = f", {len(report.adopted)} adopted" if report.adopted else ""
        return {
            "message": (
                f"Reconciled workspace '{ws_name}': {len(report.inserted)} inserted, "
                f"{len(report.updated)} updated, {len(report.removed)} removed, "
                f"{report.unchanged} unchanged{adopted_clause}."
            ),
            "count": report.present,
        }
