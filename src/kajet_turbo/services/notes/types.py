from pydantic import BaseModel


class NoteData(BaseModel):
    note_id: str
    workspace: str
    owner_id: str
    title: str
    folder: str
    tags: list[str]
    created_at: str
    updated_at: str
    content: str
