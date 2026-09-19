"""Related-notes endpoint (#211, epic #189).

Deliberately its own route, separate from ``/html`` and ``/links``: the KNN read behind it is
the slowest thing on the note page, and note content and backlinks must never wait for it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from kajet_turbo.api.schemas import RelatedNoteItem, RelatedNotesResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.dependencies import (
    CurrentUser,
    get_note_related_service,
    get_required_user,
    resolve_note_target,
)
from kajet_turbo.errors import NoteError
from kajet_turbo.services.notes.related import MAX_LIMIT, MIN_LIMIT, NoteRelatedService
from kajet_turbo.services.targets import NoteTarget

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.get(
    "/api/workspaces/{name}/notes/{note_id}/related",
    response_model=RelatedNotesResponse,
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def api_note_related(
    name: str,
    note_id: str,
    folder: Annotated[
        str | None,
        Query(description="Restrict candidates to this folder and its descendants"),
    ] = None,
    limit: Annotated[int, Query(ge=MIN_LIMIT, le=MAX_LIMIT)] = 5,
    user: CurrentUser = Depends(get_required_user),
    target: NoteTarget = Depends(resolve_note_target),
    related_service: NoteRelatedService = Depends(get_note_related_service),
) -> RelatedNotesResponse:
    result = await related_service.related_async(
        target.note_id,
        target.workspace.owner_id,
        target.workspace.name,
        folder=folder,
        limit=limit,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND)
    return RelatedNotesResponse(
        status=result.state,
        items=[RelatedNoteItem.model_validate(item, from_attributes=True) for item in result.items],
    )
