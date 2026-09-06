from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from kajet_turbo.api.schemas.base import RequestModel
from kajet_turbo.errors import ErrorCode, WorkspaceError
from kajet_turbo.shared.workspaces import WorkspaceInfoBase


def _require_name_present(data: object) -> object:
    """Runs in "before" mode (raw dict, pre field-validation) so a *missing* "name" key and
    an explicit blank/whitespace one both raise the same "workspace_name_required" custom
    error type -- letting `name` stay a genuinely required field (`min_length=1`, no
    default) for accurate OpenAPI/generated-TS, without adding it to legacy_error_codes:
    that field name is too common across the app's request models (see
    tests/api/test_error_handlers.py's own unrelated probe body, which broke when "name"
    was added there) to map safely by bare name alone, unlike
    CreateNoteRequest.title/CreateFolderRequest.path."""
    if not isinstance(data, dict):
        return data
    name = data.get("name")
    # `name is None` covers both a missing key and an explicit JSON `null` -- str(None)
    # would otherwise stringify to "None" and slip past the blank check below.
    if name is None or (isinstance(name, str) and not name.strip()):
        raise PydanticCustomError("workspace_name_required", "Name is required")
    return data


# CreateWorkspaceRequest/UpdateWorkspaceRequest's "folder" is a grouping path for the
# picker, genuinely optional and unrelated to MoveNoteRequest's required, same-named
# "folder" (notes/crud.py, a move target where "" means "move to root"). A wrong-type
# value here must not surface MoveNoteRequest's FOLDER_PATH_REQUIRED ("path is required")
# message -- each model's own legacy_error_codes entry keeps the two from colliding.
_WORKSPACE_FOLDER_ERROR_CODES: dict[str, ErrorCode] = {"folder": WorkspaceError.INVALID_INPUT}


class WorkspaceInfo(WorkspaceInfoBase):
    file_count: int = Field(description="Number of notes in this workspace")
    last_commit_at: int | None = Field(description="Unix epoch of the last commit, if any")


class WorkspacesListResponse(BaseModel):
    workspaces: list[WorkspaceInfo]


class CreateWorkspaceRequest(RequestModel):
    # REST policy: unknown fields are dropped rather than rejected (see notes/crud.py's
    # CreateNoteRequest for the same policy note).
    model_config = ConfigDict(extra="ignore")

    legacy_error_codes: ClassVar[dict[str, ErrorCode]] = _WORKSPACE_FOLDER_ERROR_CODES

    name: str = Field(min_length=1, description="Workspace name; unique per owner")
    description: str = ""
    folder: str | None = Field(
        default=None, description="Folder path for grouping this workspace in the picker"
    )
    tags: list[str] | None = None

    _require_name = model_validator(mode="before")(_require_name_present)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return v.strip()

    @field_validator("description")
    @classmethod
    def _strip_description(cls, v: str) -> str:
        return v.strip()


class CreateWorkspaceResponse(BaseModel):
    name: str


class UpdateWorkspaceRequest(RequestModel):
    model_config = ConfigDict(extra="ignore")

    legacy_error_codes: ClassVar[dict[str, ErrorCode]] = _WORKSPACE_FOLDER_ERROR_CODES

    description: str | None = None
    folder: str | None = None
    tags: list[str] | None = None


class UpdateWorkspaceResponse(BaseModel):
    name: str
    description: str
    folder: str
    tags: list[str]


class DeleteWorkspaceResponse(BaseModel):
    name: str
