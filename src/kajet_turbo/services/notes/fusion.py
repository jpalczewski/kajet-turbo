"""Pure ranking policy for note search candidates."""

from collections.abc import Iterable
from dataclasses import replace

from kajet_turbo.repositories.notes.types import ChunkHit, MetadataHit

# Fetch a wider window before filtering by folder/tags. Filtering first would require a
# dynamically-sized SQL IN clause across both virtual-table search legs.
NARROWED_CANDIDATE_LIMIT = 200
DEFAULT_CANDIDATE_LIMIT = 50
_RRF_K = 60


def fuse_hybrid(
    fts: Iterable[ChunkHit],
    vec: Iterable[ChunkHit],
    meta: Iterable[MetadataHit],
    *,
    limit: int,
    per_note_cap: int = 3,
    allowed_note_ids: set[str] | None = None,
) -> list[ChunkHit]:
    """Fuse ranked chunk candidates with note-level metadata matches using RRF."""
    fts_candidates = _filter(fts, allowed_note_ids)
    vec_candidates = _filter(vec, allowed_note_ids)
    meta_candidates = _filter(meta, allowed_note_ids)

    scores: dict[str, float] = {}
    by_id: dict[str, ChunkHit] = {}
    for candidate_list in (fts_candidates, vec_candidates):
        for rank, hit in enumerate(candidate_list):
            assert hit.chunk_id is not None
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (_RRF_K + rank)
            by_id.setdefault(hit.chunk_id, hit)

    best_chunk_for_note: dict[str, tuple[float, str]] = {}
    for chunk_id, score in scores.items():
        note_id = by_id[chunk_id].note_id
        if note_id not in best_chunk_for_note or score > best_chunk_for_note[note_id][0]:
            best_chunk_for_note[note_id] = (score, chunk_id)

    meta_matched: dict[str, list[str]] = {}
    for rank, hit in enumerate(meta_candidates):
        boost = 1.0 / (_RRF_K + rank)
        meta_matched.setdefault(hit.note_id, []).extend(hit.matched_on)
        if hit.note_id in best_chunk_for_note:
            chunk_id = best_chunk_for_note[hit.note_id][1]
            scores[chunk_id] += boost
        else:
            chunk_id = f"meta:{hit.note_id}"
            scores[chunk_id] = scores.get(chunk_id, 0.0) + boost
            by_id.setdefault(
                chunk_id,
                ChunkHit(
                    chunk_id=None,
                    note_id=hit.note_id,
                    title=hit.title,
                    folder=hit.folder,
                    updated_at=hit.updated_at,
                    header_path=[],
                    content="",
                ),
            )

    ranked = [
        replace(
            by_id[chunk_id],
            score=score,
            matched_on=meta_matched.get(by_id[chunk_id].note_id),
        )
        for chunk_id, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
    ]
    capped: list[ChunkHit] = []
    per_note: dict[str, int] = {}
    for hit in ranked:
        if per_note.get(hit.note_id, 0) >= per_note_cap:
            continue
        per_note[hit.note_id] = per_note.get(hit.note_id, 0) + 1
        capped.append(hit)
        if len(capped) >= limit:
            break
    return capped


def _filter[T: ChunkHit | MetadataHit](
    hits: Iterable[T], allowed_note_ids: set[str] | None
) -> list[T]:
    if allowed_note_ids is None:
        return list(hits)
    return [hit for hit in hits if hit.note_id in allowed_note_ids]
