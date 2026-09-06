from fastapi import APIRouter, Depends, HTTPException

from kajet_turbo.api.schemas import (
    CreateWorkspaceRequest,
    CreateWorkspaceResponse,
    DeleteWorkspaceResponse,
    UpdateWorkspaceRequest,
    UpdateWorkspaceResponse,
    WorkspacesListResponse,
)
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import (
    CurrentUser,
    get_required_user,
    get_workspace_service,
    resolve_workspace_target,
)
from kajet_turbo.errors import WorkspaceError
from kajet_turbo.services.targets import WorkspaceTarget
from kajet_turbo.services.workspaces import WorkspaceService

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.get("/api/workspaces", response_model=WorkspacesListResponse)
def api_list_workspaces(
    user: CurrentUser = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspacesListResponse:
    return WorkspacesListResponse(workspaces=ws_service.list_with_details(user.id))


@router.post(
    "/api/workspaces",
    status_code=201,
    response_model=CreateWorkspaceResponse,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def api_create_workspace(
    body: CreateWorkspaceRequest,
    user: CurrentUser = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> CreateWorkspaceResponse:
    # No resolve_workspace_target here -- this route creates a name, it doesn't address
    # an existing one, so there is nothing to authorize against yet.
    try:
        await run_sync(ws_service.create, body.name, user.id, description=body.description)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=WorkspaceError.ALREADY_EXISTS) from None
    except ValueError:
        raise HTTPException(status_code=422, detail=WorkspaceError.INVALID_INPUT) from None
    if body.folder is not None or body.tags is not None:
        try:
            await run_sync(
                ws_service.set_meta,
                user.id,
                body.name,
                folder=body.folder,
                tags=body.tags,
            )
        except ValueError:
            raise HTTPException(status_code=422, detail=WorkspaceError.INVALID_INPUT) from None
    return CreateWorkspaceResponse(name=body.name)


@router.patch(
    "/api/workspaces/{name}",
    response_model=UpdateWorkspaceResponse,
    responses={422: {"model": ErrorResponse}},
)
async def api_update_workspace(
    name: str,
    body: UpdateWorkspaceRequest,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> UpdateWorkspaceResponse:
    # exclude_unset() -> only keys the client actually sent reach set_meta(); an omitted
    # key and an explicit null both end up None here, and set_meta's repo layer already
    # treats None as "leave this column unchanged" (COALESCE), so this reproduces the same
    # "explicit null clears nothing" contract UpdateNoteRequest documents for title/content/
    # folder/tags -- unlike before #254, a *wrong-typed* key (e.g. tags as a string) now
    # 422s instead of being silently dropped by an isinstance guard.
    updates = body.model_dump(exclude_unset=True)
    try:
        result = await run_sync(ws_service.set_meta, user.id, name, **updates)
    except ValueError:
        raise HTTPException(status_code=422, detail=WorkspaceError.INVALID_INPUT) from None
    return UpdateWorkspaceResponse(name=name, **result)


@router.delete(
    "/api/workspaces/{name}",
    response_model=DeleteWorkspaceResponse,
)
async def api_delete_workspace(
    name: str,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> DeleteWorkspaceResponse:
    await run_sync(ws_service.delete, user.id, name)
    return DeleteWorkspaceResponse(name=name)
