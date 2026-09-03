from pydantic import BaseModel

from kajet_turbo.shared.notes import (
    DanglingLinkItem,
    GraphEdge,
    GraphNode,
    NoteLinkItem,
    NoteLinksBase,
)

__all__ = [
    "ChunkPreviewItem",
    "ChunkPreviewResponse",
    "DanglingLinkItem",
    "GraphEdge",
    "GraphNode",
    "GraphResponse",
    "LinksResponse",
    "NoteHtmlResponse",
    "NoteLinkItem",
    "NoteMarkdownResponse",
]


class LinksResponse(NoteLinksBase):
    pass


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    dangling_links: list[DanglingLinkItem] | None = None


class NoteHtmlResponse(BaseModel):
    note_id: str
    title: str
    folder: str
    tags: list[str]
    created_at: str
    updated_at: str
    occurred_at: str | None = None
    period: str | None = None
    content_html: str
    sha: str


class NoteMarkdownResponse(BaseModel):
    note_id: str
    title: str
    folder: str
    tags: list[str]
    created_at: str
    updated_at: str
    occurred_at: str | None = None
    period: str | None = None
    content: str
    sha: str


class ChunkPreviewItem(BaseModel):
    ordinal: int
    header_path: list[str]
    content: str
    embedded_text: str
    char_start: int
    char_end: int
    char_count: int
    embedded: bool


class ChunkPreviewResponse(BaseModel):
    note_id: str
    title: str
    index_state: str
    chunk_count: int
    chunks: list[ChunkPreviewItem]
