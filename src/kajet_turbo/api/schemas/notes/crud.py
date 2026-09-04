from typing import Literal

from pydantic import BaseModel, Field

from kajet_turbo.shared.notes import (
    FolderContext,
    MovedNoteResult,
    NoteListItem,
    ReindexResult,
    TemporalWarning,
    WikilinkWarning,
)


class NoteItem(NoteListItem):
    size_bytes: int = Field(description="Markdown content size in bytes")


class NotesListResponse(BaseModel):
    notes: list[NoteItem]


class EntriesInResponse(BaseModel):
    notes: list[NoteItem]


class CreateNoteRequest(BaseModel):
    title: str
    content: str = ""
    folder: str = ""
    tags: list[str] = []
    occurred_at: str | None = None
    period: str | None = None


class CreateNoteResponse(BaseModel):
    note_id: str
    warnings: list[WikilinkWarning] = Field(default_factory=list)


class UpdateNoteRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    folder: str | None = None
    tags: list[str] | None = None
    occurred_at: str | None = None
    period: str | None = None
    clear_date_metadata: bool = False


class UpdateNoteResponse(BaseModel):
    note_id: str
    warnings: list[WikilinkWarning] = Field(default_factory=list)
    temporal_warnings: list[TemporalWarning] = Field(default_factory=list)


class MoveNoteRequest(BaseModel):
    folder: str


class MoveNoteResponse(MovedNoteResult):
    pass


class DeleteNoteResponse(BaseModel):
    ok: bool


class NoteCreate(BaseModel):
    title: str
    content: str = ""
    tags: list[str] = []
    folder: str = ""
    occurred_at: str | None = None
    period: str | None = None


class NoteResult(BaseModel):
    index: int
    note_id: str | None = None
    error: str | None = None
    warnings: list[WikilinkWarning] = Field(default_factory=list)


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


class ReindexResponse(ReindexResult):
    pass


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


class FolderMetaResponse(FolderContext):
    pass


class UpdateFolderMetaRequest(BaseModel):
    description: str = Field(description="What this folder is for; empty string clears the field")
    instructions: str = Field(
        description="LLM instructions for working with notes in this folder; empty string clears"
    )
