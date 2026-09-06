from pydantic import BaseModel, ConfigDict, Field


class WorkspaceRemoteView(BaseModel):
    origin_url: str
    ssh_key_id: str
    enabled: bool
    dirty_at: str | None = None
    pushed_at: str | None = None
    last_error: str | None = None


class WorkspaceRemoteResponse(BaseModel):
    remote: WorkspaceRemoteView | None


class SetWorkspaceRemoteRequest(BaseModel):
    # REST policy: unknown fields are dropped rather than rejected (see notes/crud.py's
    # CreateNoteRequest for the rationale shared by every REST request model).
    model_config = ConfigDict(extra="ignore")

    origin_url: str = Field(min_length=1, description="SSH git remote URL")
    ssh_key_id: str = Field(min_length=1, description="Sealed SSH key to push with")
    enabled: bool = True
