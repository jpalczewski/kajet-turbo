from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import EntriesInResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.api.workspaces.notes._views import enrich_note_items
from kajet_turbo.dependencies import get_note_service, get_required_user, get_workspace_service
from kajet_turbo.errors import AuthError
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.workspace import InvalidFolderError

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
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    try:
        notes = note_service.entries_in(name, user["id"], period, folder)
    except ValueError, InvalidFolderError:
        raise HTTPException(status_code=422, detail="period or folder is invalid") from None
    ws_path = ws_service.workspace_path(user["id"], name)
    return JSONResponse({"notes": enrich_note_items(ws_path, notes)})
