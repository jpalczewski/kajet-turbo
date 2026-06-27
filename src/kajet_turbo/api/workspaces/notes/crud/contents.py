from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import WorkspaceContentsResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.api.workspaces.notes._views import enrich_note_items
from kajet_turbo.dependencies import get_note_service, get_required_user, get_workspace_service
from kajet_turbo.errors import AuthError, FolderError
from kajet_turbo.log import logged_route
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


def _clean_path(path: str) -> str:
    return "/".join(part for part in path.strip().strip("/").split("/") if part)


def _relative_folder(root: Path, path: Path) -> str:
    rel = path.relative_to(root)
    return "" if str(rel) == "." else "/".join(rel.parts)


def _child_folders(folders: list[str], parent: str) -> list[str]:
    prefix = f"{parent}/" if parent else ""
    depth = parent.count("/") + (1 if parent else 0)
    return sorted(
        folder
        for folder in folders
        if folder.startswith(prefix) and folder.count("/") == depth and folder != parent
    )


@router.get(
    "/api/workspaces/{name}/contents",
    response_model=WorkspaceContentsResponse,
    responses={400: {"model": ErrorResponse}},
)
@logged_route
def api_workspace_contents(
    name: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
    path: str = "",
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)

    requested_path = _clean_path(path)
    ws_path = ws_service.workspace_path(user["id"], name)
    ws_root = Path(ws_path).resolve()
    try:
        target = (ws_root / requested_path).resolve() if requested_path else ws_root
        target.relative_to(ws_root)
    except ValueError:
        raise HTTPException(status_code=400, detail=FolderError.PATH_INVALID) from None

    resolution = "missing"
    folder_path = ""
    selected_note_id = None

    if target.is_dir():
        resolution = "folder"
        folder_path = _relative_folder(ws_root, target)
    else:
        parts = requested_path.split("/") if requested_path else []
        candidate_note_id = parts[-1] if parts else ""
        candidate_folder = "/".join(parts[:-1])
        try:
            parent = (ws_root / candidate_folder).resolve() if candidate_folder else ws_root
            parent.relative_to(ws_root)
        except ValueError:
            raise HTTPException(status_code=400, detail=FolderError.PATH_INVALID) from None
        if parent.is_dir():
            folder_path = _relative_folder(ws_root, parent)
            note = note_service.get(candidate_note_id, owner_id=user["id"])
            if note is not None and note["workspace"] == name and note["folder"] == folder_path:
                resolution = "note"
                selected_note_id = candidate_note_id

    folders = note_service.list_folders(ws_path)[1:]
    notes = note_service.list_notes(name, owner_id=user["id"], folder=folder_path, limit=None)
    enriched_notes = enrich_note_items(ws_path, notes)
    default_note_id = next(
        (
            note["note_id"]
            for note in enriched_notes
            if note["title"].strip().casefold() == "readme"
        ),
        None,
    )

    return JSONResponse(
        {
            "path": requested_path,
            "resolution": resolution,
            "folder_path": folder_path,
            "selected_note_id": selected_note_id,
            "default_note_id": default_note_id,
            "folders": folders,
            "child_folders": _child_folders(folders, folder_path),
            "notes": enriched_notes,
        }
    )
