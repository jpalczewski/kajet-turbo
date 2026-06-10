from pydantic import BaseModel


class NoteItem(BaseModel):
    note_id: str
    workspace: str
    owner_id: str
    title: str
    folder: str
    tags: list[str]
    created_at: str
    updated_at: str


class NotesListResponse(BaseModel):
    notes: list[NoteItem]


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


class NoteHistoryEntry(BaseModel):
    sha: str
    message: str
    timestamp: int


class NoteHistoryResponse(BaseModel):
    entries: list[NoteHistoryEntry]
