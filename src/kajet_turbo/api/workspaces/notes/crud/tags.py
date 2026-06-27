from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import LsResponse, TagsResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.dependencies import get_note_service, get_required_user, get_workspace_service
from kajet_turbo.errors import AuthError, FolderError
from kajet_turbo.log import logged_route
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.workspace import note_filepath

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.get("/api/workspaces/{name}/tags", response_model=TagsResponse)
@logged_route
def api_list_tags(
    name: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    return JSONResponse({"tags": note_service.tag_tree(name, owner_id=user["id"])})


@router.get(
    "/api/workspaces/{name}/ls",
    response_model=LsResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
@logged_route
def api_ls(
    name: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
    path: str = "",
    recursive: bool = False,
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    ws_path = ws_service.workspace_path(user["id"], name)
    ws_root = Path(ws_path).resolve()
    try:
        folder_abs = (ws_root / path).resolve() if path else ws_root
        folder_abs.relative_to(ws_root)
    except ValueError, OSError:
        raise HTTPException(status_code=400, detail=FolderError.PATH_INVALID) from None
    if path and not folder_abs.is_dir():
        raise HTTPException(status_code=404, detail=FolderError.NOT_FOUND)
    if recursive:
        return JSONResponse({"folders": note_service.list_folders(ws_path)[1:], "entries": []})
    subdirs = sorted(
        d.name for d in folder_abs.iterdir() if d.is_dir() and not d.name.startswith(".")
    )
    notes = note_service.list_notes(name, owner_id=user["id"], folder=path, limit=1000)
    entries = []
    for note in notes:
        filepath = note_filepath(ws_path, note["folder"], note["title"])
        try:
            size_bytes = Path(filepath).stat().st_size
        except OSError:
            size_bytes = 0
        entries.append(
            {
                "note_id": note["note_id"],
                "title": note["title"],
                "size_bytes": size_bytes,
                "updated_at": note["updated_at"],
            }
        )
    return JSONResponse({"folders": subdirs, "entries": entries})
