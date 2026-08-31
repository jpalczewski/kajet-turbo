from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import ReindexResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.dependencies import get_note_service, get_required_user, get_workspace_service
from kajet_turbo.errors import AuthError, NoteError
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.post(
    "/api/workspaces/{name}/reindex",
    response_model=ReindexResponse,
    responses={409: {"model": ErrorResponse}},
)
def api_reindex_workspace(
    name: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    ws_path = ws_service.workspace_path(user["id"], name)
    try:
        result = note_service.reindex(name, owner_id=user["id"], ws_path=ws_path)
    except ValueError as e:
        raise HTTPException(
            status_code=409,
            detail={"error": str(NoteError.RECONCILE_REFUSED), "detail": str(e)},
        ) from e
    return JSONResponse(result)
