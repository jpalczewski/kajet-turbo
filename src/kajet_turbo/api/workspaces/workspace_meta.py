from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import (
    CreateWorkspaceResponse,
    UpdateWorkspaceResponse,
    WorkspacesListResponse,
)
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


@router.get("/api/workspaces", response_model=WorkspacesListResponse)
@logged_route
def api_list_workspaces(
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    return JSONResponse({"workspaces": ws_service.list_with_details(user["id"])})


@router.post(
    "/api/workspaces",
    status_code=201,
    response_model=CreateWorkspaceResponse,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
@logged_route
async def api_create_workspace(
    request,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail=WorkspaceError.INVALID_INPUT) from None
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=422, detail=WorkspaceError.NAME_REQUIRED)
    description = str(body.get("description", "")).strip()
    folder = body.get("folder")
    tags = body.get("tags")
    try:
        await run_sync(ws_service.create, name, user["id"], description=description)
    except (ValueError, FileExistsError):
        raise HTTPException(status_code=409, detail=WorkspaceError.ALREADY_EXISTS) from None
    if folder is not None or tags is not None:
        try:
            await run_sync(
                ws_service.set_meta,
                user["id"],
                name,
                folder=folder if isinstance(folder, str) else None,
                tags=tags if isinstance(tags, list) else None,
            )
        except ValueError:
            raise HTTPException(status_code=422, detail=WorkspaceError.INVALID_INPUT) from None
    return JSONResponse({"name": name}, status_code=201)


@router.patch(
    "/api/workspaces/{name}",
    response_model=UpdateWorkspaceResponse,
    responses={422: {"model": ErrorResponse}},
)
@logged_route
async def api_update_workspace(
    name: str,
    request,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail=WorkspaceError.INVALID_INPUT) from None
    description = body.get("description")
    folder = body.get("folder")
    tags = body.get("tags")
    try:
        result = await run_sync(
            ws_service.set_meta,
            user["id"],
            name,
            description=description if isinstance(description, str) else None,
            folder=folder if isinstance(folder, str) else None,
            tags=tags if isinstance(tags, list) else None,
        )
    except ValueError:
        raise HTTPException(status_code=422, detail=WorkspaceError.INVALID_INPUT) from None
    return JSONResponse({"name": name, **result})
