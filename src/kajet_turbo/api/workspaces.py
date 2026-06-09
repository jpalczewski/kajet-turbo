from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from kajet_turbo.dependencies import get_note_service, get_session_user, get_workspace_service
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService

router = APIRouter()


@router.get("/api/workspaces")
async def api_list_workspaces(
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    return JSONResponse({"workspaces": ws_service.list_for_user(user["id"])})


@router.post("/api/workspaces")
async def api_create_workspace(
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    name = str(body.get("name", "")).strip()
    if not name:
        return JSONResponse({"error": "Nazwa workspace'u jest wymagana."}, status_code=422)
    try:
        ws_service.create(name, user["id"])
    except (ValueError, FileExistsError) as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    return JSONResponse({"name": name}, status_code=201)


@router.get("/api/workspaces/{name}/notes")
async def api_list_notes(
    name: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    notes = note_service.list(name, owner_id=user["id"])
    return JSONResponse({"notes": notes})
