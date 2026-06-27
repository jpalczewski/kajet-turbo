from pydantic import BaseModel


class NoteHistoryEntry(BaseModel):
    sha: str
    message: str
    timestamp: int


class NoteHistoryResponse(BaseModel):
    entries: list[NoteHistoryEntry]


class RestoreVersionResponse(BaseModel):
    note_id: str
