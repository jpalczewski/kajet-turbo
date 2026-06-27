from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from kajet_turbo import workspace_settings as ws_settings
from kajet_turbo.api.schemas import UpdateWorkspaceSettingsResponse, WorkspaceSettingsResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import get_required_user, get_workspace_service
from kajet_turbo.errors import AuthError, WorkspaceError
from kajet_turbo.log import logged_route
from kajet_turbo.services.workspaces import WorkspaceService

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.get("/api/workspaces/{name}/settings", response_model=WorkspaceSettingsResponse)
@logged_route
async def api_get_workspace_settings(
    name: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    values = await run_sync(ws_service.get_settings, user["id"], name)
    return JSONResponse({"definitions": ws_settings.definitions(), "values": values})


@router.patch(
    "/api/workspaces/{name}/settings",
    response_model=UpdateWorkspaceSettingsResponse,
    responses={422: {"model": ErrorResponse}},
)
@logged_route
async def api_update_workspace_settings(
    name: str,
    request: Request,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail=WorkspaceError.INVALID_INPUT) from None
    values = body.get("values")
    if not isinstance(values, dict):
        raise HTTPException(status_code=422, detail=WorkspaceError.INVALID_INPUT)
    result: dict = {}
    try:
        for key, value in values.items():
            result = await run_sync(ws_service.set_setting, user["id"], name, key, value)
    except ValueError:
        raise HTTPException(status_code=422, detail=WorkspaceError.INVALID_INPUT) from None
    if not result:
        result = await run_sync(ws_service.get_settings, user["id"], name)
    return JSONResponse({"values": result})
