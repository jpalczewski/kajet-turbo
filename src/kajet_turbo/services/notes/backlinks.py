from dataclasses import replace
from functools import partial
from itertools import batched
from typing import Protocol

from sqlmodel import Session

from kajet_turbo.log import logger
from kajet_turbo.markdown import IndexedNote, LinkIndex, rewrite_wikilinks
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.notes import NoteLinkRepository, NoteRepository
from kajet_turbo.services.indexing import reindex_job_entries
from kajet_turbo.services.notes.staged_change import (
    MAX_BATCH_COMMIT_SIZE,
    StagedChange,
    commit_rows_then_tree,
)
from kajet_turbo.workspace import locate_note, path_segments, read_note_file_raw, write_note_file

# (old, new) identity of a note that was moved and/or renamed.
type NoteMove = tuple[IndexedNote, IndexedNote]


class WorkspaceLinkSnapshot(Protocol):
    @property
    def ws_name(self) -> str: ...

    @property
    def owner_id(self) -> str: ...

    @property
    def paths(self) -> tuple[IndexedNote, ...]: ...

    @property
    def index(self) -> LinkIndex: ...


class BacklinkRewriter:
    """Rewrite same-workspace wikilinks after note identity changes."""

    def __init__(
        self,
        crud_repo: NoteRepository,
        link_repo: NoteLinkRepository,
        jobs: JobRepository,
    ):
        self._crud_repo = crud_repo
        self._link_repo = link_repo
        self._jobs = jobs

    def rewrite_backlinks(
        self,
        workspace: WorkspaceLinkSnapshot,
        moves: list[NoteMove],
        ws_path: str,
        repo: GitRepository,
    ) -> None:
        """Rewrite resolved same-workspace wikilinks without changing their graph edges.

        Each chunk writes its rows before committing its staged tree change. The original
        repository is reused, preserving the caller's workspace lock and repository-open
        count. Search reindex jobs are enqueued in the same transaction as each row update.
        """
        moved = {old.note_id: new for old, new in moves}
        source_ids = self._link_repo.backlinks_many(list(moved), same_workspace=workspace.ws_name)
        if not source_ids:
            return
        after = LinkIndex(moved.get(path.note_id, path) for path in workspace.paths)
        before = workspace.index
        old_folders = {old.note_id: old.folder for old, _ in moves}

        def rewrite(target: str, before_folder: str, after_folder: str) -> str | None:
            hit = before.resolve(target, before_folder)
            if hit is None or hit.note_id not in moved:
                return None
            return after.shortest_target(
                moved[hit.note_id], after_folder, min_segments=len(path_segments(target))
            )

        message = (
            f"note: rewrite wikilink {moves[0][0].title} -> {moves[0][1].title}"
            if len(moves) == 1
            else f"note: rewrite wikilinks after moving {len(moves)} notes"
        )
        paired: list[tuple[StagedChange, tuple[str, str]]] = []
        for src in self._crud_repo.get_many(sorted(source_ids), workspace.owner_id):
            loc = locate_note(src, ws_path)
            if not loc.file_exists:
                continue
            data_meta, old_content, raw = read_note_file_raw(loc.filepath)
            new_body, changed = rewrite_wikilinks(
                old_content,
                partial(
                    rewrite,
                    before_folder=old_folders.get(src.id, src.folder),
                    after_folder=src.folder,
                ),
            )
            if not changed:
                continue
            occurred_at, period = data_meta.temporal_or(src.occurred_at, src.period)
            meta = replace(
                data_meta, id=src.id, title=src.title, occurred_at=occurred_at, period=period
            )
            item = StagedChange(
                add=loc.relative,
                remove=None,
                apply=partial(write_note_file, loc.filepath, meta, new_body),
                known_bytes=raw,
            )
            paired.append((item, (src.id, src.updated_at)))

        for chunk in batched(paired, MAX_BATCH_COMMIT_SIZE, strict=False):
            chunk_items = [item for item, _ in chunk]
            chunk_rewrites = [rewrite for _, rewrite in chunk]

            def write_rows(
                session: Session, chunk_rewrites: list[tuple[str, str]] = chunk_rewrites
            ) -> None:
                for note_id, updated_at in chunk_rewrites:
                    self._crud_repo.update_in_session(
                        session,
                        note_id,
                        owner_id=workspace.owner_id,
                        updated_at=updated_at,
                        bump_index_generation=True,
                    )
                self._jobs.enqueue_many_in_session(
                    session,
                    "reindex_note",
                    reindex_job_entries(
                        workspace.owner_id,
                        workspace.ws_name,
                        (note_id for note_id, _ in chunk_rewrites),
                    ),
                )

            commit_rows_then_tree(
                self._crud_repo,
                repo,
                chunk_items,
                message,
                operation="rewrite_backlinks",
                write_rows=write_rows,
                workspace=workspace.ws_name,
                owner_id=workspace.owner_id,
                count=len(chunk_rewrites),
                note_ids=[note_id for note_id, _ in chunk_rewrites],
            )
        logger.info(
            "backlinks_rewritten",
            ws=workspace.ws_name,
            moved=len(moves),
            sources=len(source_ids),
            rewritten=len(paired),
        )
