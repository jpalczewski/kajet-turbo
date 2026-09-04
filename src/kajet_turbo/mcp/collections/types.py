from typing import Literal

from pydantic import BaseModel


class DroppedMember(BaseModel):
    folder: str
    title: str


class CollectionResult(BaseModel):
    name: str
    grain: Literal["day", "week", "month", "year"]
    cardinality: Literal["one", "many"]
    folder: str
    title: str
    description: str | None = None


class DefineCollectionResult(BaseModel):
    name: str
    verb: Literal["add", "update"]
    would_write: bool = False
    affected_count: int
    dropped: list[DroppedMember]
    collection: CollectionResult | None = None


class DeleteCollectionResult(BaseModel):
    name: str
    deleted: bool


class OpenEntryResult(BaseModel):
    note_id: str
    folder: str
    title: str
    created: bool
    ordinal: int | None = None
    occurred_at: str | None = None
    period: str | None = None
