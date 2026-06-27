from pydantic import BaseModel


class NoteInput(BaseModel):
    title: str
    content: str = ""
    tags: list[str] = []
    folder: str = ""


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
    folder: str
    tags: list[str]
    created_at: str
    updated_at: str


class SearchChunkResult(BaseModel):
    note_id: str
    title: str
    folder: str
    header_path: list[str]
    content: str
    score: float


class NoteLinkItem(BaseModel):
    note_id: str
    title: str
    folder: str
    tags: list[str] | None = None
    updated_at: str | None = None


class NoteLinksResult(BaseModel):
    outlinks: list[NoteLinkItem]
    backlinks: list[NoteLinkItem]
