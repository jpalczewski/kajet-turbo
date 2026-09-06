import re
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from kajet_turbo.api.schemas.base import RequestModel
from kajet_turbo.errors import ErrorCode, FolderError, NoteError
from kajet_turbo.shared.notes import (
    FolderContext,
    MovedNoteResult,
    NoteListItem,
    ReindexResult,
    TemporalWarning,
    WikilinkWarning,
)

_FOLDER_PATH_RE = re.compile(r"^[a-zA-Z0-9._-][a-zA-Z0-9._\-/]*$")


def _require_title(v: str) -> str:
    """Shared by CreateNoteRequest and its batch items -- rejects a *present but blank*
    title. A missing title key never reaches this validator (required, no default) and
    is mapped back to NOTE_TITLE_REQUIRED by CreateNoteRequest.legacy_error_codes
    (api/errors.py) instead."""
    stripped = v.strip()
    if not stripped:
        raise PydanticCustomError("note_title_required", "Title is required")
    return stripped


class NoteItem(NoteListItem):
    size_bytes: int = Field(description="Markdown content size in bytes")


class NotesListResponse(BaseModel):
    notes: list[NoteItem]


class EntriesInResponse(BaseModel):
    notes: list[NoteItem]


class CreateNoteRequest(RequestModel):
    # REST policy: unknown fields are dropped rather than rejected (MCP's ToolInput
    # keeps extra="forbid" -- an LLM caller benefits from a hard error on a typo, a REST
    # client tolerating an extra field does not).
    model_config = ConfigDict(extra="ignore")

    # "title" has no default, so OpenAPI advertises it correctly as required -- but that
    # means a *missing* key never reaches _require_title (pydantic doesn't run a field
    # validator against an absent required field). legacy_error_codes maps FastAPI's own
    # missing/wrong-type error for this field back to the same legacy code instead.
    legacy_error_codes: ClassVar[dict[str, ErrorCode]] = {"title": NoteError.TITLE_REQUIRED}

    title: str = Field(min_length=1, description="Note title; unique within (workspace, folder)")
    content: str = ""
    folder: str = ""
    tags: list[str] = Field(default_factory=list)
    occurred_at: str | None = None
    period: str | None = None

    _validate_title = field_validator("title")(_require_title)


class CreateNoteResponse(BaseModel):
    note_id: str
    warnings: list[WikilinkWarning] = Field(default_factory=list)


class UpdateNoteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    content: str | None = None
    folder: str | None = None
    tags: list[str] | None = None
    occurred_at: str | None = None
    period: str | None = None
    clear_date_metadata: bool = False
    expected_sha: str | None = Field(
        default=None,
        description="The note's current HEAD sha from get_note_history -- a stale or "
        "missing value is rejected with 409 NOTE_STALE_VERSION.",
    )


class UpdateNoteResponse(BaseModel):
    note_id: str
    warnings: list[WikilinkWarning] = Field(default_factory=list)
    temporal_warnings: list[TemporalWarning] = Field(default_factory=list)


class MoveNoteRequest(RequestModel):
    # "" is itself a legitimate value here (move to root); only a genuinely missing/
    # wrong-type key hits legacy_error_codes -- an empty string still reaches the route
    # and NoteFolderService.move as a real value.
    legacy_error_codes: ClassVar[dict[str, ErrorCode]] = {"folder": FolderError.PATH_REQUIRED}

    folder: str


class MoveNoteResponse(MovedNoteResult):
    pass


class DeleteNoteResponse(BaseModel):
    ok: bool


class NoteResult(BaseModel):
    index: int
    note_id: str | None = None
    error: str | None = None
    warnings: list[WikilinkWarning] = Field(default_factory=list)


class BatchCreateNotesRequest(BaseModel):
    notes: list[CreateNoteRequest] = Field(min_length=1, max_length=50)


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


class CreateFolderRequest(RequestModel):
    # "path" is required (no default), so a missing key never reaches _validate_path
    # below -- legacy_error_codes maps FastAPI's own missing/wrong-type error for this
    # field back to the same legacy code the validator raises for a present-but-blank one.
    legacy_error_codes: ClassVar[dict[str, ErrorCode]] = {"path": FolderError.PATH_REQUIRED}

    path: str

    @field_validator("path")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        path = v.strip().strip("/")
        if not path:
            raise PydanticCustomError("folder_path_required", "Path is required")
        segments = path.split("/")
        if any(not s or s in (".", "..") for s in segments):
            raise PydanticCustomError("folder_path_invalid", "Invalid folder path")
        if not _FOLDER_PATH_RE.match(path):
            raise PydanticCustomError("folder_path_invalid", "Invalid folder path")
        return path


class CreateFolderResponse(BaseModel):
    path: str


class FolderMetaResponse(FolderContext):
    pass


class UpdateFolderMetaRequest(BaseModel):
    description: str = Field(description="What this folder is for; empty string clears the field")
    instructions: str = Field(
        description="LLM instructions for working with notes in this folder; empty string clears"
    )
