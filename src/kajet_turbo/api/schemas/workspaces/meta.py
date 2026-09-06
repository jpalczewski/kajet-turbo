from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from kajet_turbo.shared.workspaces import WorkspaceInfoBase


def _require_name_present(data: object) -> object:
    """Runs in "before" mode (raw dict, pre field-validation) so a *missing* "name" key and
    an explicit blank/whitespace one both raise the same "workspace_name_required" custom
    error type -- letting `name` stay a genuinely required field (`min_length=1`, no
    default) for accurate OpenAPI/generated-TS, without keying api/errors.py's global
    by-field-name `_REQUIRED_FIELD_CODES` table on "name": that field name is too common
    across the app's request models (see tests/api/test_error_handlers.py's own unrelated
    probe body, which broke when "name" was added there) to map safely at that scope,
    unlike CreateNoteRequest.title/CreateFolderRequest.path."""
    if not isinstance(data, dict):
        return data
    name = data.get("name")
    # `name is None` covers both a missing key and an explicit JSON `null` -- str(None)
    # would otherwise stringify to "None" and slip past the blank check below.
    if name is None or (isinstance(name, str) and not name.strip()):
        raise PydanticCustomError("workspace_name_required", "Name is required")
    return data


class WorkspaceInfo(WorkspaceInfoBase):
    file_count: int = Field(description="Number of notes in this workspace")
    last_commit_at: int | None = Field(description="Unix epoch of the last commit, if any")


class WorkspacesListResponse(BaseModel):
    workspaces: list[WorkspaceInfo]


class CreateWorkspaceRequest(BaseModel):
    # REST policy: unknown fields are dropped rather than rejected (see notes/crud.py's
    # CreateNoteRequest for the same policy note).
    model_config = ConfigDict(extra="ignore")

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


class UpdateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

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
