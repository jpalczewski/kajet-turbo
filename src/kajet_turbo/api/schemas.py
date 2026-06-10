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
    size_bytes: int


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


class WorkspaceInfo(BaseModel):
    name: str
    file_count: int
    last_commit_at: int | None


class WorkspacesListResponse(BaseModel):
    workspaces: list[WorkspaceInfo]


class LsEntry(BaseModel):
    note_id: str
    title: str
    size_bytes: int
    updated_at: str


class LsResponse(BaseModel):
    folders: list[str]
    entries: list[LsEntry]


class NoteHistoryEntry(BaseModel):
    sha: str
    message: str
    timestamp: int


class NoteHistoryResponse(BaseModel):
    entries: list[NoteHistoryEntry]
