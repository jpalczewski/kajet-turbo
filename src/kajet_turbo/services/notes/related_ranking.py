"""Pure ranking for related-notes over stored-vector evidence (#210, decided in #209).

No DB, no I/O — a source chunk's evidence rows are already fetched (a correlated vec0
self-join, one row per (source chunk, target note) with the best matching target chunk and
raw L2 distance); this module turns that evidence into one ranked list per source note.

Unlike ``scripts/bench_related.py``'s ``agg_hub_head``, this ranks in raw L2 distance space,
not cosine: the bench formula assumes unit-length vectors (``cos = 1 - d^2/2``), but nothing
in ``embedding/cache.py``/``embedding/base.py`` normalizes stored vectors. The hub-correction
trick needs no such assumption — smaller distance is still "more similar" in either space:

For each source chunk s, let H_s be its 10 nearest distinct target notes (by best distance)
and r_s the mean distance over H_s. margin(s, t) = r_s - distance(s, t) (positive: t is
closer to s than s's own typical neighbour). A target's score is the best margin it earns
from any source that has it in its head, plus a coverage boost for appearing across more
source chunks. Targets seen only past some source's head rank after every head target, by
their own best margin (fill only) — see ``HEAD``/``COVERAGE_WEIGHT``.
"""

from dataclasses import dataclass

from kajet_turbo.repositories.notes.types import RelatedEvidence

HEAD = 10
COVERAGE_WEIGHT = 0.10


@dataclass(frozen=True, slots=True)
class RankedNote:
    """One ranked target note. ``is_head`` distinguishes a note some source's top-10
    nearest reached (ranked by ``score``) from one only seen past some source's head
    (fill, ranked after every head note by ``margin`` alone) — the two are not
    comparable on ``score``."""

    note_id: str
    source_chunk_id: str
    best_chunk_id: str
    best_distance: float
    hub_margin: float
    coverage: float
    is_head: bool
    score: float


def rank_related(evidence: list[RelatedEvidence], n_sources: int) -> list[RankedNote]:
    """Rank targets from raw evidence. ``n_sources`` is the number of source chunks that
    actually have a vector under the active identity (``source_chunks_embedded``, not
    ``source_chunks_used``'s pre-cap pick count and not the note's total chunk count) —
    coverage is a fraction of chunks the self-join could actually query, not of chunks
    merely selected by the spread cap."""
    if not evidence or n_sources <= 0:
        return []

    by_source: dict[str, list[RelatedEvidence]] = {}
    for row in evidence:
        by_source.setdefault(row.source_chunk_id, []).append(row)

    # best[note] = (margin, source_chunk_id, chunk_id, distance) from whichever source's
    # head gave the largest margin; hits counts how many sources' heads the note
    # appeared in.
    best: dict[str, tuple[float, str, str, float]] = {}
    hits: dict[str, int] = {}
    tail: dict[str, tuple[float, str, str, float]] = {}

    for rows in by_source.values():
        ranked = sorted(rows, key=lambda r: (r.distance, r.target_note_id))
        # One row per (source, target) already (SQL groups by target note), so no
        # per-target dedup is needed before slicing the head.
        head, rest = ranked[:HEAD], ranked[HEAD:]
        r_s = sum(row.distance for row in head) / len(head)
        for row in head:
            margin = r_s - row.distance
            current = best.get(row.target_note_id)
            if current is None or margin > current[0]:
                best[row.target_note_id] = (
                    margin,
                    row.source_chunk_id,
                    row.target_chunk_id,
                    row.distance,
                )
            hits[row.target_note_id] = hits.get(row.target_note_id, 0) + 1
        for row in rest:
            margin = r_s - row.distance
            current = tail.get(row.target_note_id)
            if row.target_note_id not in best and (current is None or margin > current[0]):
                tail[row.target_note_id] = (
                    margin,
                    row.source_chunk_id,
                    row.target_chunk_id,
                    row.distance,
                )

    head_notes = [
        RankedNote(
            note_id=note_id,
            source_chunk_id=source_chunk_id,
            best_chunk_id=chunk_id,
            best_distance=distance,
            hub_margin=margin,
            coverage=hits[note_id] / n_sources,
            is_head=True,
            score=margin + COVERAGE_WEIGHT * hits[note_id] / n_sources,
        )
        for note_id, (margin, source_chunk_id, chunk_id, distance) in best.items()
    ]
    tail_notes = [
        RankedNote(
            note_id=note_id,
            source_chunk_id=source_chunk_id,
            best_chunk_id=chunk_id,
            best_distance=distance,
            hub_margin=margin,
            coverage=0.0,
            is_head=False,
            score=margin,
        )
        for note_id, (margin, source_chunk_id, chunk_id, distance) in tail.items()
        if note_id not in best
    ]

    # note_id ascending keeps ties deterministic; head sorts entirely before tail
    # regardless of the two groups' unrelated score scales (see RankedNote.is_head).
    head_notes.sort(key=lambda n: (-n.score, n.note_id))
    tail_notes.sort(key=lambda n: (-n.score, n.note_id))
    return head_notes + tail_notes
