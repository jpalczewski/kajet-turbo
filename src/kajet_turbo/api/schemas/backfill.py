from typing import Literal

from pydantic import BaseModel


class TemporalBackfillCandidate(BaseModel):
    note_id: str
    title: str
    folder: str
    field: Literal["occurred_at", "period"]
    value: str
    sha: str | None


class TemporalBackfillSkipped(BaseModel):
    note_id: str
    reason: str


class TemporalBackfillAmbiguous(TemporalBackfillSkipped):
    title: str
    folder: str


class TemporalBackfillPreviewResponse(BaseModel):
    candidates: list[TemporalBackfillCandidate]
    ambiguous: list[TemporalBackfillAmbiguous]
    skipped: list[TemporalBackfillSkipped]


class ApplyTemporalBackfillRequest(BaseModel):
    candidates: list[TemporalBackfillCandidate]


class ApplyTemporalBackfillResponse(BaseModel):
    applied: int
