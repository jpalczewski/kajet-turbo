from typing import Literal

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


class CreateNoteRequest(BaseModel):
    title: str
    content: str = ""
    folder: str = ""
    tags: list[str] = []


class CreateNoteResponse(BaseModel):
    note_id: str


class UpdateNoteRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    folder: str | None = None
    tags: list[str] | None = None


class UpdateNoteResponse(BaseModel):
    note_id: str


class MoveNoteRequest(BaseModel):
    folder: str


class MoveNoteResponse(BaseModel):
    note_id: str
    folder: str


class DeleteNoteResponse(BaseModel):
    ok: bool


class NoteCreate(BaseModel):
    title: str
    content: str = ""
    tags: list[str] = []
    folder: str = ""


class NoteResult(BaseModel):
    index: int
    note_id: str | None = None
    error: str | None = None


class BatchCreateNotesRequest(BaseModel):
    notes: list[NoteCreate]


class BatchCreateNotesResponse(BaseModel):
    results: list[NoteResult]


class WorkspaceContentsResponse(BaseModel):
    path: str
    resolution: Literal["folder", "note", "missing"]
    folder_path: str
    selected_note_id: str | None
    default_note_id: str | None
    folders: list[str]
    child_folders: list[str]
    notes: list[NoteItem]


class ReindexResponse(BaseModel):
    message: str
    count: int


class TagNode(BaseModel):
    path: str
    name: str
    exact_count: int
    descendant_count: int


class TagsResponse(BaseModel):
    tags: list[TagNode]


class CreateFolderRequest(BaseModel):
    path: str


class CreateFolderResponse(BaseModel):
    path: str
