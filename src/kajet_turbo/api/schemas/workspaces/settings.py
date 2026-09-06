from pydantic import BaseModel, ConfigDict, StrictBool


class SettingDefinition(BaseModel):
    key: str
    type: str
    label: str
    description: str
    default: object


class WorkspaceSettingsResponse(BaseModel):
    definitions: list[SettingDefinition]
    values: dict


class UpdateWorkspaceSettingsValues(BaseModel):
    """One optional field per `kajet_turbo.workspace_settings.REGISTRY` key -- add a field
    here whenever a setting is added there. `extra="forbid"` turns an unknown setting key
    into a 422 instead of the silent no-op a plain dict would give it (this is the one
    REST body in the family that deliberately opts out of the extra="ignore" default --
    a setting key is a small, enumerable, versioned set, not a client-tolerant surface).
    `StrictBool` (not `bool`) so a JSON string like "yes" 422s instead of being coerced --
    matching `workspace_settings._check_type`'s exact-bool check.
    """

    model_config = ConfigDict(extra="forbid")

    include_in_search_all: StrictBool | None = None
    validate_links: StrictBool | None = None


class UpdateWorkspaceSettingsRequest(BaseModel):
    values: UpdateWorkspaceSettingsValues


class UpdateWorkspaceSettingsResponse(BaseModel):
    values: dict
