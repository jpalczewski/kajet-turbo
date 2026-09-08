from fastapi import APIRouter, Depends, HTTPException

from kajet_turbo.api.schemas import EntriesInResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.api.workspaces.notes._views import enrich_note_items
from kajet_turbo.dependencies import (
    CurrentUser,
    get_note_temporal_service,
    get_required_user,
    resolve_workspace_target,
)
from kajet_turbo.errors import NoteError
from kajet_turbo.services.notes import NoteTemporalService
from kajet_turbo.services.targets import WorkspaceTarget

router = APIRouter(responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}})


@router.get(
    "/api/workspaces/{name}/entries",
    response_model=EntriesInResponse,
    responses={422: {"model": ErrorResponse}},
)
def api_entries_in(
    name: str,
    period: str,
    folder: str | None = None,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    note_temporal_service: NoteTemporalService = Depends(get_note_temporal_service),
) -> EntriesInResponse:
    try:
        notes = note_temporal_service.entries_in(name, user.id, period, folder)
    except ValueError:
        raise HTTPException(status_code=422, detail=NoteError.INVALID_INPUT) from None
    return EntriesInResponse(notes=enrich_note_items(str(workspace.path), notes))
