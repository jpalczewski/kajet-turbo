from pydantic import BaseModel

from kajet_turbo.shared.collections import CollectionResult
from kajet_turbo.shared.notes import NoteListItem


class CollectionsListResponse(BaseModel):
    collections: list[CollectionResult]


class CollectionEntriesResponse(BaseModel):
    notes: list[NoteListItem]
