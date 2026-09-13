"""Related notes from already-stored chunk vectors — no embedding call, no job, no cache
write (#210, part of epic #189). Direct-call shape, no write-service delegate — same
pattern as ``NoteGraphService`` (see ``services/notes/CLAUDE.md``, "Service boundaries").
"""

from collections.abc import Callable

from kajet_turbo.concurrency import related_notes_limiter, run_sync
from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.identity import IndexIdentity
from kajet_turbo.log import logger
from kajet_turbo.repositories.notes import (
    NoteChunkRepository,
    NoteRepository,
    RelatedNoteItem,
    RelatedNotesResult,
)
from kajet_turbo.repositories.notes.chunks import RELATED_K
from kajet_turbo.services.notes.related_ranking import RankedNote, rank_related

MIN_LIMIT = 1
MAX_LIMIT = 50


class NoteRelatedService:
    def __init__(
        self,
        chunk_repo: NoteChunkRepository,
        note_repo: NoteRepository,
        resolve_backend: Callable[[str], EmbedderConfig | None],
    ):
        self._chunk_repo = chunk_repo
        self._note_repo = note_repo
        self._resolve_backend = resolve_backend

    def related(
        self,
        note_id: str,
        owner_id: str,
        workspace: str,
        *,
        folder: str | None = None,
        limit: int = 5,
    ) -> RelatedNotesResult | None:
        """Sync: runs entirely on the calling (worker) thread. Returns ``None`` when the
        note doesn't exist or isn't owned by ``owner_id`` in ``workspace`` — same
        convention as ``NoteReadService.get`` — so the caller can turn that into 404
        instead of a fifth state."""
        if not MIN_LIMIT <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be between {MIN_LIMIT} and {MAX_LIMIT}, got {limit}")
        note = self._note_repo.get(note_id, owner_id=owner_id)
        if note is None or note.workspace != workspace:
            return None
        cfg = self._resolve_cfg(owner_id)
        if cfg is None:
            return RelatedNotesResult("unavailable", [], 0, 0, RELATED_K)
        identity = IndexIdentity.from_config(cfg)
        folder_note_ids = (
            self._note_repo.note_ids_under_folder(workspace, owner_id, folder)
            if folder is not None
            else None
        )
        query = self._chunk_repo.related_chunks(
            note_id, workspace, owner_id, identity, folder_note_ids=folder_note_ids
        )
        if query.source_chunks_total == 0:
            return RelatedNotesResult("empty", [], 0, 0, query.k)
        if query.source_chunks_embedded == 0:
            # Chunks exist but none have a vector under the active identity yet — either
            # never embedded, or embedded under a since-switched profile. The self-join's
            # identity filter (chunks.py:_related_src_sql) is what makes this distinction
            # correct without a separate per-identity freshness table (see #210's plan).
            # This is NOT the same as "evidence is empty" — a note whose chunks ARE all
            # embedded but simply have no related notes yet is `ready` with zero items.
            return RelatedNotesResult(
                "pending", [], query.source_chunks_total, query.source_chunks_used, query.k
            )
        ranked = rank_related(query.evidence, query.source_chunks_embedded)[:limit]
        items = self._hydrate(ranked, owner_id)
        logger.info(
            "related_notes_computed",
            source_chunks_total=query.source_chunks_total,
            source_chunks_used=query.source_chunks_used,
            items=len(items),
        )
        return RelatedNotesResult(
            "ready", items, query.source_chunks_total, query.source_chunks_used, query.k
        )

    async def related_async(
        self,
        note_id: str,
        owner_id: str,
        workspace: str,
        *,
        folder: str | None = None,
        limit: int = 5,
    ) -> RelatedNotesResult | None:
        """Gates the whole DB-bound read behind a small per-process semaphore before
        dispatching to ``run_sync`` (#209's benchmark: this read is expensive enough — a
        correlated vec0 self-join, up to 800 evidence rows — to fill every pool-limiter
        slot under light concurrency and starve every other request)."""
        async with related_notes_limiter():
            return await run_sync(
                self.related, note_id, owner_id, workspace, folder=folder, limit=limit
            )

    def _resolve_cfg(self, owner_id: str) -> EmbedderConfig | None:
        try:
            return self._resolve_backend(owner_id)
        except Exception as e:
            logger.opt(exception=e).warning("related_notes_resolve_failed", owner_id=owner_id)
            return None

    def _hydrate(self, ranked: list[RankedNote], owner_id: str) -> list[RelatedNoteItem]:
        """Fetch note metadata and chunk fragments only for the final top-N — never for
        the full evidence set. A note or chunk missing here means a concurrent edit/delete
        raced the query above; drop that item rather than surface it half-filled."""
        if not ranked:
            return []
        notes_by_id = {
            note.id: note
            for note in self._note_repo.get_many([r.note_id for r in ranked], owner_id)
        }
        target_fragments = self._chunk_repo.get_chunk_fragments_by_id(
            [r.best_chunk_id for r in ranked]
        )
        source_fragments = self._chunk_repo.get_chunk_fragments_by_rowid(
            [r.source_rowid for r in ranked]
        )
        items = []
        for r in ranked:
            note = notes_by_id.get(r.note_id)
            target = target_fragments.get(r.best_chunk_id)
            source = source_fragments.get(r.source_rowid)
            if note is None or target is None or source is None:
                continue
            items.append(
                RelatedNoteItem(
                    note_id=note.id,
                    title=note.title,
                    folder=note.folder,
                    updated_at=note.updated_at,
                    source_chunk_id=source.id,
                    target_chunk_id=target.id,
                    source_header_path=source.header_path,
                    target_header_path=target.header_path,
                    target_content=target.content,
                    best_distance=r.best_distance,
                    hub_margin=r.hub_margin,
                    coverage=r.coverage,
                    score=r.score,
                )
            )
        return items
