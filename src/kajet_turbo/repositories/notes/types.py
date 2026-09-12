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


@dataclass(frozen=True, slots=True)
class MetadataHit:
    """A note-level match that FTS and vector indices cannot represent."""

    note_id: str
    title: str
    folder: str
    updated_at: str
    matched_on: list[MetadataMatch]
