from pydantic import BaseModel


class SettingDefinition(BaseModel):
    key: str
    type: str
    label: str
    description: str
    default: object


class WorkspaceSettingsResponse(BaseModel):
    definitions: list[SettingDefinition]
    values: dict


class UpdateWorkspaceSettingsResponse(BaseModel):
    values: dict
