from pydantic import BaseModel

from kajet_turbo.errors import ErrorCode


class ErrorResponse(BaseModel):
    error: ErrorCode
    detail: str | None = None
