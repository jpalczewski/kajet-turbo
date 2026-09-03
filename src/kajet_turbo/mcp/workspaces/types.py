import enum
from typing import Literal

from pydantic import BaseModel

from kajet_turbo import workspace_settings
from kajet_turbo.shared.workspaces import WorkspaceInfoBase

SettingKey = enum.Enum("SettingKey", {k: k for k in workspace_settings.REGISTRY})


class WorkspaceInfo(WorkspaceInfoBase):
    pass


class WorkspacesResult(BaseModel):
    workspaces: list[WorkspaceInfo]


class WorkspaceMessageResult(BaseModel):
    message: str
    workspace: str


class WorkspaceUpdatedResult(WorkspaceMessageResult):
    description: str | None = None
    tags: list[str] | None = None


class WorkspaceSettingInfo(BaseModel):
    key: str
    label: str
    description: str
    type: Literal["bool"]
    value: bool
    default: bool


class WorkspaceSettingsResult(BaseModel):
    settings: list[WorkspaceSettingInfo]


class WorkspaceSettingUpdatedResult(BaseModel):
    message: str
    workspace: str
    setting: str
    value: bool | int | str
