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


class WorkspaceInfo(BaseModel):
    name: str
    file_count: int
    last_commit_at: int | None
    description: str = ""
    folder: str = ""
    tags: list[str] = []


class UpdateWorkspaceResponse(BaseModel):
    name: str
    description: str
    folder: str
    tags: list[str]


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


class SessionResponse(BaseModel):
    email: str


class LoginResponse(BaseModel):
    email: str
    redirect_uri: str | None = None


class OkResponse(BaseModel):
    ok: bool


class CreateWorkspaceResponse(BaseModel):
    name: str


class ConsentResponse(BaseModel):
    redirect_uri: str


class PendingInfoResponse(BaseModel):
    client_name: str


class RestoreVersionResponse(BaseModel):
    note_id: str


class CreateFolderRequest(BaseModel):
    path: str


class CreateFolderResponse(BaseModel):
    path: str


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


class EmbeddingProfileItem(BaseModel):
    id: str
    name: str
    base_url: str
    model: str
    dim: int
    is_active: bool
    has_key: bool


class EmbeddingProfilesResponse(BaseModel):
    profiles: list[EmbeddingProfileItem]


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


class NoteCreate(BaseModel):
    title: str
    content: str = ""
    tags: list[str] = []
    folder: str = ""


class BatchCreateNotesRequest(BaseModel):
    notes: list[NoteCreate]


class NoteResult(BaseModel):
    index: int
    note_id: str | None = None
    error: str | None = None


class BatchCreateNotesResponse(BaseModel):
    results: list[NoteResult]


class SshKeyItem(BaseModel):
    id: str
    name: str
    algorithm: str
    fingerprint: str
    public_key: str
    created_at: str


class SshKeysResponse(BaseModel):
    keys: list[SshKeyItem]


class WorkspaceRemoteView(BaseModel):
    origin_url: str
    ssh_key_id: str
    enabled: bool
    dirty_at: str | None = None
    pushed_at: str | None = None
    last_error: str | None = None


class WorkspaceRemoteResponse(BaseModel):
    remote: WorkspaceRemoteView | None


class JobItem(BaseModel):
    id: str
    kind: str
    workspace: str | None = None
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None = None
    next_run_at: float
    created_at: float
    updated_at: float


class JobsResponse(BaseModel):
    jobs: list[JobItem]
