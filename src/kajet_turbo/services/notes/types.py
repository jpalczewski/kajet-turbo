from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from kajet_turbo.markdown import EditSpec
from kajet_turbo.shared.notes import TemporalWarning, WikilinkWarning


class ResultModel(BaseModel):
    """Typed service result with a temporary read-only mapping bridge.

    Production callers use attributes and variant matching. The bridge keeps direct
    service consumers working while they migrate from the former dict contract.
    """

    __hash__ = None  # Pydantic result models are mutable and therefore unhashable.

    def __getitem__(self, key: str) -> Any:
        # The legacy mapping bridge cannot express field-dependent return types.
        return self.model_dump()[key]

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in type(self).model_fields

    def get(self, key: str, default: object = None) -> Any:
        return self.model_dump().get(key, default)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, dict):
            return self.model_dump() == other
        return super().__eq__(other)


@dataclass(frozen=True, slots=True)
class EditBatchItem:
    """One item of ``NoteEditService.edit_many``'s batch: the note-identifying/gating fields
    plus the edit payload, typed all the way from ``NoteEditInput`` instead of round-tripped
    through a string-keyed dict.

    ``edit`` is an ``EditSpec``, whose own ``mode`` default is ``"overwrite"`` (it mirrors
    ``apply_edit``'s single-note semantics, not this batch's). ``NoteEditInput.to_edit_spec()``
    is what actually carries the batch-safe ``"append"`` default forward — a caller building
    an ``EditBatchItem`` directly, bypassing ``NoteEditInput``, must set ``mode`` explicitly.
    """

    note_id: str
    expected_sha: str
    edit: EditSpec
    tags: list[str] | None = None
    occurred_at: str | None = None
    period: str | None = None
    clear_date_metadata: bool = False


@dataclass(frozen=True, slots=True)
class DeleteBatchItem:
    """One item of ``NoteDeleteService.delete_many``'s batch, typed all the way from
    ``NoteDeleteInput`` instead of round-tripped through a string-keyed dict."""

    note_id: str
    expected_sha: str


class NoteData(BaseModel):
    note_id: str
    workspace: str
    owner_id: str
    title: str
    folder: str
    tags: list[str]
    created_at: str
    updated_at: str
    occurred_at: str | None
    period: str | None
    extras: dict[str, object]
    content: str
    sha: str


# Write-result models live at the service boundary. MCP re-exports them because its
# wire vocabulary already uses these shapes; keeping them here prevents the service
# layer from depending on an adapter package.
class SavedNoteResult(ResultModel):
    note_id: str
    warnings: list[WikilinkWarning] = Field(default_factory=list)
    occurred_at: str | None = None
    period: str | None = None


class StaleVersion(ResultModel):
    note_id: str
    error: str

    def __getitem__(self, key: str) -> object:
        if key == "stale_sha":
            return True
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        return key == "stale_sha" or super().__contains__(key)

    def get(self, key: str, default: object = None) -> object:
        if key == "stale_sha":
            return True
        return super().get(key, default)


class DeletedNoteResult(ResultModel):
    note_id: str


class EditNoteSuccess(SavedNoteResult):
    replaced: int | None = None
    temporal_warnings: list[TemporalWarning] = Field(
        default_factory=list,
        description="occurred_at/period fields that had an unparseable value on disk "
        "(a hand edit, most often) and were kept at their previous value instead.",
    )


class BatchNoteSuccess(ResultModel):
    index: int
    note_id: str
    warnings: list[WikilinkWarning] = Field(default_factory=list)


class BatchNoteError(ResultModel):
    index: int
    error: str


class EditNotesSuccessItem(BatchNoteSuccess):
    replaced: int | None = None
    temporal_warnings: list[TemporalWarning] = Field(default_factory=list)


class EditNotesApplied(ResultModel):
    applied: Literal[True] = True
    results: list[EditNotesSuccessItem]


class EditNotesError(ResultModel):
    index: int
    note_id: str
    error: str


class EditNotesRejected(ResultModel):
    applied: Literal[False] = False
    errors: list[EditNotesError] = Field(
        description="The entire batch was rejected; no changes were saved."
    )


class DeleteNotesApplied(ResultModel):
    applied: Literal[True] = True
    results: list[BatchNoteSuccess]


class DeleteNotesError(ResultModel):
    index: int
    note_id: str
    error: str


class DeleteNotesRejected(ResultModel):
    applied: Literal[False] = False
    errors: list[DeleteNotesError] = Field(
        description="The entire batch was rejected; no notes were deleted."
    )
