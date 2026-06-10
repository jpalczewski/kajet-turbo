import bleach
import mistune
from pathlib import Path
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

_ALLOWED_TAGS = [
    *bleach.sanitizer.ALLOWED_TAGS,
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "pre", "code", "blockquote",
    "ul", "ol", "li", "hr",
    "table", "thead", "tbody", "tr", "th", "td",
    "img",
]
_ALLOWED_ATTRS = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def _render_html(content: str) -> str:
    return bleach.clean(
        mistune.html(content),
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )

from kajet_turbo.api.schemas import (
    LsEntry, LsResponse,
    NoteHistoryResponse, NoteHtmlResponse, NoteMarkdownResponse,
    NotesListResponse, WorkspacesListResponse,
)
from kajet_turbo.dependencies import get_note_service, get_session_user, get_workspace_service
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.workspace import note_filepath

router = APIRouter()


@router.get("/api/workspaces", response_model=WorkspacesListResponse)
def api_list_workspaces(
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    return JSONResponse({"workspaces": ws_service.list_with_details(user["id"])})


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


@router.get("/api/workspaces/{name}/notes", response_model=NotesListResponse)
def api_list_notes(
    name: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
    folder: str | None = None,
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    ws_path = ws_service.workspace_path(user["id"], name)
    notes = note_service.list(name, owner_id=user["id"], folder=folder)
    enriched = []
    for note in notes:
        filepath = note_filepath(ws_path, note["folder"], note["title"])
        try:
            size_bytes = Path(filepath).stat().st_size
        except OSError:
            size_bytes = 0
        enriched.append({**note, "size_bytes": size_bytes})
    return JSONResponse({"notes": enriched})


@router.get("/api/workspaces/{name}/ls", response_model=LsResponse)
def api_ls(
    name: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
    path: str = "",
    recursive: bool = False,
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)

    ws_path = ws_service.workspace_path(user["id"], name)
    folder_abs = Path(ws_path, *path.split("/")) if path else Path(ws_path)

    if path and not folder_abs.is_dir():
        return JSONResponse({"error": "Folder not found"}, status_code=404)

    if recursive:
        all_folders = note_service.list_folders(name, user["id"])
        expanded: set[str] = set()
        for folder in all_folders:
            parts = folder.split("/")
            for i in range(1, len(parts) + 1):
                expanded.add("/".join(parts[:i]))
        return JSONResponse({"folders": sorted(expanded), "entries": []})

    subdirs = sorted(
        d.name for d in folder_abs.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )
    notes = note_service.list(name, owner_id=user["id"], folder=path, limit=1000)
    entries = []
    for note in notes:
        filepath = note_filepath(ws_path, note["folder"], note["title"])
        try:
            size_bytes = Path(filepath).stat().st_size
        except OSError:
            size_bytes = 0
        entries.append({
            "note_id": note["note_id"],
            "title": note["title"],
            "size_bytes": size_bytes,
            "updated_at": note["updated_at"],
        })
    return JSONResponse({"folders": subdirs, "entries": entries})


@router.get("/api/workspaces/{name}/notes/{note_id}/html", response_model=NoteHtmlResponse)
def api_get_note_html(
    name: str,
    note_id: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    ws_path = ws_service.workspace_path(user["id"], name)
    note = note_service.get_with_content(note_id, owner_id=user["id"], ws_path=ws_path)
    if note is None:
        return JSONResponse({"error": "Notatka nie istnieje."}, status_code=404)
    return JSONResponse({
        "note_id": note["note_id"],
        "title": note["title"],
        "folder": note["folder"],
        "tags": note["tags"],
        "created_at": note["created_at"],
        "updated_at": note["updated_at"],
        "content_html": _render_html(note["content"]),
    })


@router.get("/api/workspaces/{name}/notes/{note_id}/markdown", response_model=NoteMarkdownResponse)
def api_get_note_markdown(
    name: str,
    note_id: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    ws_path = ws_service.workspace_path(user["id"], name)
    note = note_service.get_with_content(note_id, owner_id=user["id"], ws_path=ws_path)
    if note is None:
        return JSONResponse({"error": "Notatka nie istnieje."}, status_code=404)
    return JSONResponse({
        "note_id": note["note_id"],
        "title": note["title"],
        "folder": note["folder"],
        "tags": note["tags"],
        "created_at": note["created_at"],
        "updated_at": note["updated_at"],
        "content": note["content"],
    })


@router.get("/api/workspaces/{name}/notes/{note_id}/history", response_model=NoteHistoryResponse)
def api_note_history(
    name: str,
    note_id: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    ws_path = ws_service.workspace_path(user["id"], name)
    try:
        entries = note_service.get_history(note_id, owner_id=user["id"], ws_path=ws_path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse({"entries": entries})


@router.get("/api/workspaces/{name}/notes/{note_id}/history/{sha}", response_model=NoteHtmlResponse)
def api_note_version(
    name: str,
    note_id: str,
    sha: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    from kajet_turbo.repositories.git import GitError
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    ws_path = ws_service.workspace_path(user["id"], name)
    try:
        version = note_service.get_version(note_id, sha, owner_id=user["id"], ws_path=ws_path)
    except (ValueError, GitError) as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse({
        "note_id": version["note_id"],
        "title": version["title"],
        "folder": version["folder"],
        "tags": version["tags"],
        "created_at": version["created_at"],
        "updated_at": version["updated_at"],
        "content_html": _render_html(version["content"]),
    })


@router.post("/api/workspaces/{name}/notes/{note_id}/history/{sha}/restore")
async def api_restore_note_version(
    name: str,
    note_id: str,
    sha: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    ws_path = ws_service.workspace_path(user["id"], name)
    try:
        result = note_service.restore_version(note_id, sha, owner_id=user["id"], ws_path=ws_path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse(result)
