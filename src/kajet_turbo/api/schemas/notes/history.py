from pydantic import BaseModel, Field

from kajet_turbo.shared.notes import HistoryEntry, WikilinkWarning


class NoteHistoryEntry(HistoryEntry):
    pass


class NoteHistoryResponse(BaseModel):
    entries: list[NoteHistoryEntry]


class RestoreVersionResponse(BaseModel):
    note_id: str
    warnings: list[WikilinkWarning] = Field(default_factory=list)
