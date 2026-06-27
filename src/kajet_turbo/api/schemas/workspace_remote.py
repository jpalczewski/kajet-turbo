from pydantic import BaseModel


class WorkspaceRemoteView(BaseModel):
    origin_url: str
    ssh_key_id: str
    enabled: bool
    dirty_at: str | None = None
    pushed_at: str | None = None
    last_error: str | None = None


class WorkspaceRemoteResponse(BaseModel):
    remote: WorkspaceRemoteView | None
