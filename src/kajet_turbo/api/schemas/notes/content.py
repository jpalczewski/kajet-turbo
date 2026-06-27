from pydantic import BaseModel


class NoteLinkItem(BaseModel):
    note_id: str
    title: str
    folder: str


class LinksResponse(BaseModel):
    backlinks: list[NoteLinkItem]
    outlinks: list[NoteLinkItem]


class NoteHtmlResponse(BaseModel):
    note_id: str
    title: str
    folder: str
    tags: list[str]
    created_at: str
    updated_at: str
    content_html: str


class NoteMarkdownResponse(BaseModel):
    note_id: str
    title: str
    folder: str
    tags: list[str]
    created_at: str
    updated_at: str
    content: str


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
