from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NoteInput(BaseModel):
    title: str = Field(description="Note title; unique within (workspace, folder)")
    content: str = Field(default="", description="Markdown body; use [[Title]] or [[Folder/Title]] for wikilinks, [[note:ID]] for cross-workspace links")
    tags: list[str] = Field(default=[], description="Tag list, e.g. ['work', 'work/projects']")
    folder: str = Field(default="", description="Folder path, e.g. 'Projects/Client A'; empty string = workspace root")


class SavedNoteResult(BaseModel):
    note_id: str


class MovedNoteResult(BaseModel):
    note_id: str
    folder: str


class ConflictItem(BaseModel):
    title: str
    folder: str


class MovedFolderResult(BaseModel):
    moved: int
    src: str
    dst: str


class FolderConflictResult(BaseModel):
    error: str
    conflicts: list[ConflictItem]


class DeletedNoteResult(BaseModel):
    note_id: str


class ReindexResult(BaseModel):
    message: str
    count: int


class PrunedFoldersResult(BaseModel):
    pruned: list[str]
    count: int


class TagOperationResult(BaseModel):
    note_id: str
    tags: list[str]
    frontmatter_tags: list[str]
    warnings: list[str]


class TagItem(BaseModel):
    path: str
    name: str
    count: int


class HistoryEntry(BaseModel):
    sha: str
    message: str
    timestamp: int


class NoteListItem(BaseModel):
    note_id: str
    workspace: str
    owner_id: str
    title: str
    folder: str = Field(description="Folder path; empty string means workspace root")
    tags: list[str]
    created_at: str
    updated_at: str


class FolderContext(BaseModel):
    """Metadata for the folder being listed, surfaced passively to LLMs."""

    model_config = ConfigDict(from_attributes=True)

    path: str = Field(description="Folder path; empty string means workspace root")
    description: str = Field(description="What this folder is for")
    instructions: str = Field(description="LLM instructions for working with notes in this folder")


class NoteListResponse(BaseModel):
    notes: list[NoteListItem]
    folder_context: FolderContext | None = Field(
        default=None,
        description="Metadata for the queried folder, present when a folder filter was given and metadata exists",
    )


class FolderInfo(BaseModel):
    """Folder with its description, returned by list_folders."""

    model_config = ConfigDict(from_attributes=True)

    path: str = Field(description="Folder path; empty string means workspace root")
    description: str = Field(description="What this folder is for; empty when not set")


class SearchChunkResult(BaseModel):
    note_id: str
    title: str
    folder: str
    header_path: list[str]
    content: str
    score: float


class NoteLinkItem(BaseModel):
    note_id: str = Field(description="Use in [[note:NOTE_ID]] to create a permanent cross-workspace link")
    title: str
    folder: str
    workspace: str | None = Field(default=None, description="Non-null and != active workspace means cross-workspace link; reference with [[note:note_id]]")
    tags: list[str] | None = None
    updated_at: str | None = None


class NoteLinksResult(BaseModel):
    outlinks: list[NoteLinkItem]
    backlinks: list[NoteLinkItem]


class BatchNoteSuccess(BaseModel):
    index: int
    note_id: str


class BatchNoteError(BaseModel):
    index: int
    error: str


class ConfirmationRequired(BaseModel):
    note_id: str
    requires_confirmation: Literal[True]
    would_remove_tags: list[str] = Field(
        description="Tags that would be removed by this operation"
    )
    overwrites_content: bool = Field(
        description="Whether non-empty content would be overwritten"
    )
    warning: str = Field(
        description="Human-readable warning; explain to the user what will change and ask to confirm"
    )


class Cancelled(BaseModel):
    note_id: str
    cancelled: Literal[True]
    message: str


class EditNoteSuccess(BaseModel):
    note_id: str
