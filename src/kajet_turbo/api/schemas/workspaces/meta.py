from pydantic import BaseModel


class WorkspaceInfo(BaseModel):
    name: str
    file_count: int
    last_commit_at: int | None
    description: str = ""
    folder: str = ""
    tags: list[str] = []


class WorkspacesListResponse(BaseModel):
    workspaces: list[WorkspaceInfo]


class CreateWorkspaceResponse(BaseModel):
    name: str


class UpdateWorkspaceResponse(BaseModel):
    name: str
    description: str
    folder: str
    tags: list[str]
