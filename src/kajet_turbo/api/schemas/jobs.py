from pydantic import BaseModel


class JobItem(BaseModel):
    id: str
    kind: str
    workspace: str | None = None
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None = None
    next_run_at: float
    created_at: float
    updated_at: float


class JobsResponse(BaseModel):
    jobs: list[JobItem]
