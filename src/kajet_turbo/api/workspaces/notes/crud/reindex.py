from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import ReindexResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.dependencies import get_note_service, get_required_user, get_workspace_service
from kajet_turbo.errors import AuthError
from kajet_turbo.log import logged_route
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.post("/api/workspaces/{name}/reindex", response_model=ReindexResponse)
@logged_route
def api_reindex_workspace(
    name: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    ws_path = ws_service.workspace_path(user["id"], name)
    result = note_service.reindex(name, owner_id=user["id"], ws_path=ws_path)
    return JSONResponse(result)
