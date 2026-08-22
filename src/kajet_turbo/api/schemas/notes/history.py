from pydantic import BaseModel, Field

from kajet_turbo.api.schemas.notes.crud import WikilinkWarning


class NoteHistoryEntry(BaseModel):
    sha: str
    message: str
    timestamp: int


class NoteHistoryResponse(BaseModel):
    entries: list[NoteHistoryEntry]


class RestoreVersionResponse(BaseModel):
    note_id: str
    warnings: list[WikilinkWarning] = Field(default_factory=list)
