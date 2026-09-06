from fastapi import APIRouter, Depends, HTTPException

from kajet_turbo.api.schemas import (
    OkResponse,
    SetWorkspaceRemoteRequest,
    WorkspaceRemoteResponse,
    WorkspaceRemoteView,
)
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import (
    CurrentUser,
    get_required_user,
    get_workspace_remote_service,
    resolve_workspace_target,
)
from kajet_turbo.errors import WorkspaceRemoteError
from kajet_turbo.services.targets import WorkspaceTarget
from kajet_turbo.services.workspace_remote import WorkspaceRemoteService

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.get("/api/workspaces/{name}/remote", response_model=WorkspaceRemoteResponse)
def api_get_workspace_remote(
    name: str,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    svc: WorkspaceRemoteService = Depends(get_workspace_remote_service),
) -> WorkspaceRemoteResponse:
    row = svc.get(user.id, workspace.name)
    return WorkspaceRemoteResponse(remote=WorkspaceRemoteView(**row) if row else None)


@router.put(
    "/api/workspaces/{name}/remote",
    response_model=WorkspaceRemoteResponse,
    responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def api_set_workspace_remote(
    name: str,
    body: SetWorkspaceRemoteRequest,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    svc: WorkspaceRemoteService = Depends(get_workspace_remote_service),
) -> WorkspaceRemoteResponse:
    # ValueError here is domain-level (bad URL scheme, unknown ssh_key_id) -- blank-field
    # validation already 422s declaratively via SetWorkspaceRemoteRequest's min_length.
    try:
        result = await run_sync(
            svc.set,
            user.id,
            workspace.name,
            origin_url=body.origin_url,
            ssh_key_id=body.ssh_key_id,
            enabled=body.enabled,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": str(WorkspaceRemoteError.INVALID_INPUT), "detail": str(e)},
        ) from None
    return WorkspaceRemoteResponse(remote=WorkspaceRemoteView(**result))


@router.delete(
    "/api/workspaces/{name}/remote",
    response_model=OkResponse,
    responses={404: {"model": ErrorResponse}},
)
def api_delete_workspace_remote(
    name: str,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    svc: WorkspaceRemoteService = Depends(get_workspace_remote_service),
) -> OkResponse:
    if not svc.delete(user.id, workspace.name):
        raise HTTPException(status_code=404, detail=WorkspaceRemoteError.NOT_FOUND)
    return OkResponse(ok=True)


@router.post(
    "/api/workspaces/{name}/remote/push",
    response_model=OkResponse,
    responses={400: {"model": ErrorResponse}},
)
def api_trigger_workspace_push(
    name: str,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    svc: WorkspaceRemoteService = Depends(get_workspace_remote_service),
) -> OkResponse:
    if not svc.trigger_push(user.id, workspace.name):
        raise HTTPException(status_code=400, detail=WorkspaceRemoteError.NOT_CONFIGURED)
    return OkResponse(ok=True)
