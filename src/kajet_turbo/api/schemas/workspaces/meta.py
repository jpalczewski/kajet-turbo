from pydantic import BaseModel, Field

from kajet_turbo.shared.workspaces import WorkspaceInfoBase


class WorkspaceInfo(WorkspaceInfoBase):
    file_count: int = Field(description="Number of notes in this workspace")
    last_commit_at: int | None = Field(description="Unix epoch of the last commit, if any")


class WorkspacesListResponse(BaseModel):
    workspaces: list[WorkspaceInfo]


class CreateWorkspaceResponse(BaseModel):
    name: str


class UpdateWorkspaceResponse(BaseModel):
    name: str
    description: str
    folder: str
    tags: list[str]


class DeleteWorkspaceResponse(BaseModel):
    name: str
