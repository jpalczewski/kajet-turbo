from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import WorkspaceRemoteResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import (
    get_session_user,
    get_workspace_remote_service,
    get_workspace_service,
)
from kajet_turbo.log import logged_route
from kajet_turbo.services.workspace_remote import WorkspaceRemoteService
from kajet_turbo.services.workspaces import WorkspaceService

router = APIRouter()


def _guard(request: Request, name: str, ws_service: WorkspaceService):
    """Return (user, None) on success or (None, JSONResponse) on auth/access failure."""
    user = get_session_user(request)
    if not user:
        return None, JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return None, JSONResponse({"error": "Brak dostępu."}, status_code=403)
    return user, None


@router.get("/api/workspaces/{name}/remote", response_model=WorkspaceRemoteResponse)
@logged_route
def api_get_workspace_remote(
    name: str,
    request: Request,
    svc: WorkspaceRemoteService = Depends(get_workspace_remote_service),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    user, deny = _guard(request, name, ws_service)
    if deny:
        return deny
    return JSONResponse({"remote": svc.get(user["id"], name)})


@router.put("/api/workspaces/{name}/remote")
@logged_route
async def api_set_workspace_remote(
    name: str,
    request: Request,
    svc: WorkspaceRemoteService = Depends(get_workspace_remote_service),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    user, deny = _guard(request, name, ws_service)
    if deny:
        return deny
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    try:
        result = await run_sync(
            svc.set,
            user["id"],
            name,
            origin_url=body.get("origin_url", ""),
            ssh_key_id=body.get("ssh_key_id", ""),
            enabled=bool(body.get("enabled", True)),
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"remote": result})


@router.delete("/api/workspaces/{name}/remote")
@logged_route
def api_delete_workspace_remote(
    name: str,
    request: Request,
    svc: WorkspaceRemoteService = Depends(get_workspace_remote_service),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    user, deny = _guard(request, name, ws_service)
    if deny:
        return deny
    if not svc.delete(user["id"], name):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({"ok": True})


@router.post("/api/workspaces/{name}/remote/push")
@logged_route
def api_trigger_workspace_push(
    name: str,
    request: Request,
    svc: WorkspaceRemoteService = Depends(get_workspace_remote_service),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    user, deny = _guard(request, name, ws_service)
    if deny:
        return deny
    if not svc.trigger_push(user["id"], name):
        return JSONResponse({"error": "No enabled remote configured"}, status_code=400)
    return JSONResponse({"ok": True})
