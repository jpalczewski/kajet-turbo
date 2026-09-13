from dataclasses import dataclass
from typing import Literal

type MetadataMatch = Literal["title", "tag", "folder"]


@dataclass(frozen=True, slots=True)
class StoredChunk:
    """A persisted chunk, decoded into the shape consumers need outside the database."""

    id: str
    ordinal: int
    header_path: list[str]
    content: str
    char_start: int
    char_end: int
    dim: int | None

    def __getitem__(self, key: str) -> object:
        """Compatibility bridge for internal callers migrating from row dictionaries."""
        return getattr(self, key)


@dataclass(frozen=True, slots=True)
class ChunkHit:
    """A chunk candidate or fused search result.

    ``chunk_id`` is internal ranking identity. It is intentionally not exposed by the
    MCP response model.
    """

    chunk_id: str | None
    note_id: str
    title: str
    folder: str
    updated_at: str
    header_path: list[str]
    content: str
    score: float = 0.0
    matched_on: list[MetadataMatch] | None = None

    def __getitem__(self, key: str) -> object:
        """Compatibility bridge for internal callers migrating from row dictionaries."""
        return getattr(self, key)


@dataclass(frozen=True, slots=True)
class MetadataHit:
    """A note-level match that FTS and vector indices cannot represent."""

    note_id: str
    title: str
    folder: str
    updated_at: str
    matched_on: list[MetadataMatch]

    def __getitem__(self, key: str) -> object:
        """Compatibility bridge for internal callers migrating from row dictionaries."""
        return getattr(self, key)


@dataclass(frozen=True, slots=True)
class RelatedEvidence:
    """One (source chunk, target note) pair from the related-notes self-join: the
    target's closest chunk to that source chunk, already MIN-aggregated in SQL.
    ``distance`` is raw L2, not a derived similarity."""

    source_rowid: int
    target_note_id: str
    target_chunk_id: str
    distance: float


@dataclass(frozen=True, slots=True)
class RelatedChunkQuery:
    """Raw related-notes evidence for one source note, before ranking.

    ``source_chunks_used`` is how many chunks the spread cap picked (#209's
    "whether the cap applied"); ``source_chunks_embedded`` is how many of THOSE actually
    have a vector under the active identity — the number the self-join could possibly
    have queried. The two are not the same: a source_chunks_embedded of 0 with a nonzero
    source_chunks_total means "pending" (content exists, nothing embedded under this
    identity yet); a positive source_chunks_embedded with empty ``evidence`` means
    "ready" with zero results (nothing else in the partition matched) — collapsing the
    two into one "evidence empty" signal would report a genuinely empty result as
    pending.
    """

    source_chunks_total: int
    source_chunks_used: int
    source_chunks_embedded: int
    k: int
    evidence: list[RelatedEvidence]


type RelatedNotesState = Literal["ready", "pending", "unavailable", "empty"]


@dataclass(frozen=True, slots=True)
class RelatedNoteItem:
    """One ranked related note, ready for REST/MCP to wrap in their own response model.

    ``best_distance``/``hub_margin``/``coverage`` are the raw calibration metrics #214
    will use to evaluate a quality threshold — never converted to a percentage here.
    """

    note_id: str
    title: str
    folder: str
    updated_at: str
    source_chunk_id: str
    target_chunk_id: str
    source_header_path: list[str]
    target_header_path: list[str]
    target_content: str
    best_distance: float
    hub_margin: float
    coverage: float
    score: float

    def __getitem__(self, key: str) -> object:
        """Compatibility bridge for internal callers migrating from row dictionaries."""
        return getattr(self, key)


@dataclass(frozen=True, slots=True)
class RelatedNotesResult:
    """The shared related-notes response contract: one owner-scoped, workspace/identity
    -scoped read, with an explicit readiness state instead of collapsing "not ready" into
    an empty list (see #210's design doc)."""

    state: RelatedNotesState
    items: list[RelatedNoteItem]
    source_chunks_total: int
    source_chunks_used: int
    k: int
