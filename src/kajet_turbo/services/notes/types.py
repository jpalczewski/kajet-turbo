from dataclasses import dataclass

from pydantic import BaseModel

from kajet_turbo.markdown import EditSpec


@dataclass(frozen=True, slots=True)
class EditBatchItem:
    """One item of ``NoteService.edit_many``'s batch: the note-identifying/gating fields
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
    content: str
    sha: str
