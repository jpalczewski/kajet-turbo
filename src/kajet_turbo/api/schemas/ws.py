from typing import Annotated, Literal

from pydantic import BaseModel, Field


class NoteUpdatedEvent(BaseModel):
    type: Literal["note_updated"]
    owner_id: str
    workspace: str
    note_id: str
    updated_at: str


class WorkspaceChangedEvent(BaseModel):
    type: Literal["workspace_changed"]
    owner_id: str
    workspace: str


ServerEvent = Annotated[NoteUpdatedEvent | WorkspaceChangedEvent, Field(discriminator="type")]


class PingMessage(BaseModel):
    type: Literal["ping"]


ClientMessage = Annotated[PingMessage, Field(discriminator="type")]
