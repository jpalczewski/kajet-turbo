from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NoteInput(BaseModel):
    title: str = Field(description="Note title; unique within (workspace, folder)")
    content: str = Field(
        default="",
        description="Markdown body; use [[Title]] or [[Folder/Title]] for wikilinks, [[note:ID]] for cross-workspace links",
    )
    tags: list[str] = Field(default=[], description="Tag list, e.g. ['work', 'work/projects']")
    folder: str = Field(
        default="",
        description="Folder path, e.g. 'Projects/Client A'; empty string = workspace root",
    )


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
    updated_at: str
    header_path: list[str]
    content: str
    score: float
    matched_on: list[Literal["title", "tag", "folder"]] | None = Field(
        default=None,
        description=(
            "Non-null when this hit was surfaced by an exact metadata match "
            "(title/tag/folder), not only full-text/semantic ranking."
        ),
    )


class NoteLinkItem(BaseModel):
    note_id: str = Field(
        description="Use in [[note:NOTE_ID]] to create a permanent cross-workspace link"
    )
    title: str
    folder: str
    workspace: str | None = Field(
        default=None,
        description="Non-null and != active workspace means cross-workspace link; reference with [[note:note_id]]",
    )
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
    would_remove_tags: list[str] = Field(description="Tags that would be removed by this operation")
    overwrites_content: bool = Field(description="Whether non-empty content would be overwritten")
    warning: str = Field(
        description="Human-readable warning; explain to the user what will change and ask to confirm"
    )


class Cancelled(BaseModel):
    note_id: str
    cancelled: Literal[True]
    message: str


class StaleVersion(BaseModel):
    note_id: str
    error: str


class EditNoteSuccess(BaseModel):
    note_id: str
    replaced: int | None = None


class GrepMatch(BaseModel):
    note_id: str
    title: str
    folder: str
    line_number: int
    line: str


class GrepResult(BaseModel):
    matches: list[GrepMatch]
    truncated: bool = Field(
        description="True if max_results was hit — more matches may exist beyond what's returned."
    )


class NoteReadError(BaseModel):
    note_id: str
    error: str


class OutlineSectionItem(BaseModel):
    level: int
    heading: str
    target_heading: str
    line_start: int
    line_end: int
    char_start: int
    char_end: int
    section_chars: int
    section_lines: int
    ambiguous: bool = Field(
        description="True when this heading text repeats elsewhere in the note — "
        "edit_note's target_heading lookup would raise an ambiguity error."
    )


class NoteOutlineResult(BaseModel):
    note_id: str
    title: str
    folder: str
    updated_at: str
    total_chars: int
    total_lines: int
    preamble_chars: int
    preamble_lines: int
    sections: list[OutlineSectionItem]


class OmittedNote(BaseModel):
    note_id: str
    title: str
    chars: int


class FolderExportResult(BaseModel):
    markdown: str
    note_count: int
    total_chars: int
    truncated: bool
    omitted: list[OmittedNote] = Field(
        description="Notes excluded once max_chars was hit (empty when nothing was truncated)."
    )


class NoteEditInput(BaseModel):
    note_id: str = Field(description="id notatki do edycji")
    expected_sha: str = Field(
        description="Aktualny HEAD sha notatki z get_note/get_note_history — dowód, że przed "
        "edycją widziałeś bieżącą wersję. Niezgodność odrzuca cały batch."
    )
    mode: Literal[
        "overwrite",
        "append",
        "prepend",
        "replace_section",
        "replace_text",
        "insert_after",
        "delete_text",
    ] = Field(
        default="append",
        description="Jak w edit_note. Domyślnie 'append' (najmniej destrukcyjny) — w batchu "
        "łatwo o pomyłkę przy 'overwrite' na wielu notatkach naraz.",
    )
    content: str = ""
    target_heading: str | None = None
    old_text: str | None = None
    replace_all: bool = False
    tags: list[str] | None = Field(
        default=None, description="Podmienia frontmatter tags tej notatki; None = bez zmian."
    )


class EditNotesSuccessItem(BaseModel):
    index: int
    note_id: str
    replaced: int | None = None


class EditNotesApplied(BaseModel):
    applied: Literal[True]
    results: list[EditNotesSuccessItem]


class EditNotesError(BaseModel):
    index: int
    note_id: str
    error: str


class EditNotesRejected(BaseModel):
    applied: Literal[False]
    errors: list[EditNotesError] = Field(
        description="Cały batch odrzucony — nic nie zostało zapisane."
    )


class EditNotesDestructiveItem(BaseModel):
    index: int
    note_id: str
    would_remove_tags: list[str]
    overwrites_content: bool


class EditNotesConfirmationRequired(BaseModel):
    applied: Literal[False]
    requires_confirmation: Literal[True]
    items: list[EditNotesDestructiveItem]
    warning: str


class NoteDeleteInput(BaseModel):
    note_id: str = Field(description="id notatki do usunięcia")
    expected_sha: str = Field(
        description="Aktualny HEAD sha notatki z get_note_history — dowód, że przed "
        "usunięciem widziałeś bieżącą wersję. Niezgodność odrzuca cały batch."
    )


class DeleteNotesApplied(BaseModel):
    applied: Literal[True]
    results: list[BatchNoteSuccess]


class DeleteNotesError(BaseModel):
    index: int
    note_id: str
    error: str


class DeleteNotesRejected(BaseModel):
    applied: Literal[False]
    errors: list[DeleteNotesError] = Field(
        description="Cały batch odrzucony — nic nie zostało usunięte."
    )
