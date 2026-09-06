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


def _reject_wrong_type_folder(v: object) -> object:
    """Runs in "before" mode so a wrong-type "folder" raises a model-specific custom error
    type ("workspace_folder_invalid") instead of Pydantic's generic "string_type", which
    would otherwise collide with api/errors.py's global by-field-name `_REQUIRED_FIELD_CODES`
    table -- that table already maps a bare "folder" key to `FolderError.PATH_REQUIRED` for
    MoveNoteRequest's unrelated, *required* "folder" field (notes/crud.py), where "" is a
    legitimate move-to-root value and only a missing/wrong-type key should 422. This
    workspace "folder" field is genuinely optional (grouping path for the picker), so a
    wrong-type value here must not surface the misleading "path is required" message."""
    if v is not None and not isinstance(v, str):
        raise PydanticCustomError("workspace_folder_invalid", "Folder must be a string")
    return v


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

    @field_validator("folder", mode="before")
    @classmethod
    def _validate_folder(cls, v: object) -> object:
        return _reject_wrong_type_folder(v)

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

    @field_validator("folder", mode="before")
    @classmethod
    def _validate_folder(cls, v: object) -> object:
        return _reject_wrong_type_folder(v)


class UpdateWorkspaceResponse(BaseModel):
    name: str
    description: str
    folder: str
    tags: list[str]


class DeleteWorkspaceResponse(BaseModel):
    name: str
