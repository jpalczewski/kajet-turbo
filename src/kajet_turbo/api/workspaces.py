import re
from pathlib import Path

import bleach
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from kajet_turbo import workspace_settings
from kajet_turbo.api.schemas import (
    BatchCreateNotesResponse,
    ChunkPreviewResponse,
    CreateFolderResponse,
    CreateNoteResponse,
    CreateWorkspaceResponse,
    DeleteNoteResponse,
    LinksResponse,
    LsResponse,
    MoveNoteResponse,
    NoteHistoryResponse,
    NoteHtmlResponse,
    NoteMarkdownResponse,
    NotesListResponse,
    ReindexResponse,
    RestoreVersionResponse,
    TagsResponse,
    UpdateNoteResponse,
    UpdateWorkspaceResponse,
    UpdateWorkspaceSettingsResponse,
    WorkspaceSettingsResponse,
    WorkspacesListResponse,
)
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import get_note_service, get_session_user, get_workspace_service
from kajet_turbo.log import logged_route, logger
from kajet_turbo.markdown import BrokenWikilinkError, LinkResolver, render_markdown
from kajet_turbo.repositories.git import GitError
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.workspace import InvalidFolderError, note_filepath

_ALLOWED_TAGS = [
    *bleach.sanitizer.ALLOWED_TAGS,
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "pre",
    "code",
    "blockquote",
    "ul",
    "ol",
    "li",
    "hr",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "img",
    "span",
]
_ALLOWED_ATTRS = {
    **bleach.sanitizer.ALLOWED_ATTRIBUTES,
    "a": ["href", "title", "class"],
    "img": ["src", "alt", "title"],
    "span": ["class"],
}
_ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

_FOLDER_PATH_RE = re.compile(r"^[a-zA-Z0-9._-][a-zA-Z0-9._\-/]*$")


def _render_html(
    content: str, resolver: LinkResolver | None = None, slug: str | None = None
) -> str:
    return bleach.clean(
        render_markdown(content, resolver, slug),
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )


router = APIRouter()


@router.get("/api/workspaces", response_model=WorkspacesListResponse)
@logged_route
def api_list_workspaces(
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    return JSONResponse({"workspaces": ws_service.list_with_details(user["id"])})


@router.post("/api/workspaces", status_code=201, response_model=CreateWorkspaceResponse)
@logged_route
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
    description = str(body.get("description", "")).strip()
    folder = body.get("folder")
    tags = body.get("tags")
    try:
        await run_sync(ws_service.create, name, user["id"], description=description)
    except (ValueError, FileExistsError) as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    if folder is not None or tags is not None:
        try:
            await run_sync(
                ws_service.set_meta,
                user["id"],
                name,
                folder=folder if isinstance(folder, str) else None,
                tags=tags if isinstance(tags, list) else None,
            )
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=422)
    return JSONResponse({"name": name}, status_code=201)


@router.patch("/api/workspaces/{name}", response_model=UpdateWorkspaceResponse)
@logged_route
async def api_update_workspace(
    name: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
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
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return JSONResponse({"name": name, **result})


@router.get("/api/workspaces/{name}/settings", response_model=WorkspaceSettingsResponse)
@logged_route
async def api_get_workspace_settings(
    name: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    values = await run_sync(ws_service.get_settings, user["id"], name)
    return JSONResponse({"definitions": workspace_settings.definitions(), "values": values})


@router.patch("/api/workspaces/{name}/settings", response_model=UpdateWorkspaceSettingsResponse)
@logged_route
async def api_update_workspace_settings(
    name: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    values = body.get("values")
    if not isinstance(values, dict):
        return JSONResponse({"error": "Pole 'values' jest wymagane."}, status_code=422)
    result: dict = {}
    try:
        for key, value in values.items():
            result = await run_sync(ws_service.set_setting, user["id"], name, key, value)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    if not result:
        result = await run_sync(ws_service.get_settings, user["id"], name)
    return JSONResponse({"values": result})


@router.get("/api/workspaces/{name}/notes", response_model=NotesListResponse)
@logged_route
def api_list_notes(
    name: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
    folder: str | None = None,
    tag: str | None = None,
    include_descendants: bool = True,
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    ws_path = ws_service.workspace_path(user["id"], name)
    if tag is not None:
        notes = note_service.notes_by_tag(
            name, user["id"], tag, include_descendants=include_descendants
        )
    else:
        # Explorer lists the full folder; no cap (MCP list_notes keeps its own limit).
        notes = note_service.list_notes(name, owner_id=user["id"], folder=folder, limit=None)
    enriched = []
    for note in notes:
        filepath = note_filepath(ws_path, note["folder"], note["title"])
        try:
            size_bytes = Path(filepath).stat().st_size
        except OSError:
            size_bytes = 0
        enriched.append({**note, "size_bytes": size_bytes})
    return JSONResponse({"notes": enriched})


@router.post("/api/workspaces/{name}/reindex", response_model=ReindexResponse)
@logged_route
def api_reindex_workspace(
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
    ws_path = ws_service.workspace_path(user["id"], name)
    result = note_service.reindex(name, owner_id=user["id"], ws_path=ws_path)
    return JSONResponse(result)


@router.get("/api/workspaces/{name}/tags", response_model=TagsResponse)
@logged_route
def api_list_tags(
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
    return JSONResponse({"tags": note_service.tag_tree(name, owner_id=user["id"])})


@router.get("/api/workspaces/{name}/ls", response_model=LsResponse)
@logged_route
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
    ws_root = Path(ws_path).resolve()
    try:
        folder_abs = (ws_root / path).resolve() if path else ws_root
        folder_abs.relative_to(ws_root)
    except ValueError, OSError:
        return JSONResponse({"error": "Invalid path"}, status_code=400)

    if path and not folder_abs.is_dir():
        return JSONResponse({"error": "Folder not found"}, status_code=404)

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


@router.post("/api/workspaces/{name}/folders", response_model=CreateFolderResponse)
@logged_route
async def api_create_folder(
    name: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    from kajet_turbo.repositories.git import GitRepository

    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    path = str(body.get("path", "")).strip().strip("/")
    if not path:
        return JSONResponse({"error": "Ścieżka jest wymagana."}, status_code=422)
    segments = path.split("/")
    if any(not s or s in (".", "..") for s in segments):
        return JSONResponse({"error": "Niedozwolona ścieżka."}, status_code=422)
    if not _FOLDER_PATH_RE.match(path):
        return JSONResponse({"error": "Niedozwolone znaki w ścieżce."}, status_code=422)
    ws_path = ws_service.workspace_path(user["id"], name)
    ws_root = Path(ws_path).resolve()
    target = (ws_root / path).resolve()
    try:
        target.relative_to(ws_root)
    except ValueError:
        return JSONResponse({"error": "Niedozwolona ścieżka."}, status_code=422)
    gitkeep = target / ".gitkeep"
    gitkeep.parent.mkdir(parents=True, exist_ok=True)
    if not gitkeep.exists():
        gitkeep.touch()
        relative = str(gitkeep.relative_to(ws_root))
        try:
            await run_sync(
                lambda: GitRepository(ws_path).commit_file(relative, f"folder: add {path}")
            )
        except GitError as e:
            gitkeep.unlink(missing_ok=True)
            return JSONResponse({"error": str(e)}, status_code=500)
    logger.info("folder_created", ws=name, path=path)
    return JSONResponse({"path": path})


@router.post("/api/workspaces/{name}/notes", status_code=201, response_model=CreateNoteResponse)
@logged_route
async def api_create_note(
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
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    title = str(body.get("title", "")).strip()
    if not title:
        return JSONResponse({"error": "Tytuł jest wymagany."}, status_code=422)
    content = str(body.get("content", ""))
    folder = str(body.get("folder", ""))
    tags = body.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    ws_path = ws_service.workspace_path(user["id"], name)
    try:
        result = await run_sync(
            note_service.save, user["id"], name, ws_path, title, content, tags, folder=folder
        )
    except BrokenWikilinkError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse(result, status_code=201)


@router.post(
    "/api/workspaces/{name}/notes/batch",
    response_model=BatchCreateNotesResponse,
)
@logged_route
async def api_create_notes_batch(
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
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    notes = body.get("notes")
    if not isinstance(notes, list) or not notes:
        return JSONResponse({"error": "notes nie może być puste."}, status_code=422)
    ws_path = ws_service.workspace_path(user["id"], name)
    try:
        results = await run_sync(note_service.save_many, user["id"], name, ws_path, notes)
    except GitError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"results": results}, status_code=200)


@router.patch("/api/workspaces/{name}/notes/{note_id}", response_model=UpdateNoteResponse)
@logged_route
async def api_update_note(
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
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    title = body.get("title")
    content = body.get("content")
    tags = body.get("tags")
    folder = body.get("folder")
    ws_path = ws_service.workspace_path(user["id"], name)
    try:
        result = await run_sync(
            note_service.update,
            note_id,
            owner_id=user["id"],
            ws_path=ws_path,
            title=title,
            content=content,
            tags=tags,
            folder=folder,
            # Human-driven editor: an explicit save IS the consent. The destructive-op
            # confirmation gate exists to stop the agent (MCP) from accidentally
            # overwriting; it must not block the user's own UI.
            confirm=True,
        )
    except (InvalidFolderError, BrokenWikilinkError) as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except FileExistsError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except (ValueError, FileNotFoundError) as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse(result)


@router.post(
    "/api/workspaces/{name}/notes/{note_id}/move",
    response_model=MoveNoteResponse,
)
@logged_route
async def api_move_note(
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
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    folder = body.get("folder")
    if not isinstance(folder, str):
        return JSONResponse({"error": "Folder jest wymagany."}, status_code=422)

    ws_path = ws_service.workspace_path(user["id"], name)
    try:
        result = await run_sync(
            note_service.move,
            note_id,
            owner_id=user["id"],
            ws_path=ws_path,
            folder=folder,
        )
    except InvalidFolderError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except (ValueError, FileNotFoundError) as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except FileExistsError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse(result)


@router.delete("/api/workspaces/{name}/notes/{note_id}", response_model=DeleteNoteResponse)
@logged_route
async def api_delete_note(
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
        await run_sync(note_service.delete, note_id, owner_id=user["id"], ws_path=ws_path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return JSONResponse({"ok": True})


@router.get("/api/workspaces/{name}/notes/{note_id}/html", response_model=NoteHtmlResponse)
@logged_route
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
        logger.warning(
            "note_html_access_denied",
            user_id=user["id"],
            email=user.get("email"),
            workspace=name,
            note_id=note_id,
        )
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    ws_path = ws_service.workspace_path(user["id"], name)
    note = note_service.get_with_content(note_id, owner_id=user["id"], ws_path=ws_path)
    if note is None:
        return JSONResponse({"error": "Notatka nie istnieje."}, status_code=404)
    return JSONResponse(
        {
            "note_id": note["note_id"],
            "title": note["title"],
            "folder": note["folder"],
            "tags": note["tags"],
            "created_at": note["created_at"],
            "updated_at": note["updated_at"],
            "content_html": _render_html(
                note["content"],
                resolver=note_service.link_resolver(name, user["id"]),
                slug=name,
            ),
        }
    )


@router.get("/api/workspaces/{name}/notes/{note_id}/markdown", response_model=NoteMarkdownResponse)
@logged_route
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
    return JSONResponse(
        {
            "note_id": note["note_id"],
            "title": note["title"],
            "folder": note["folder"],
            "tags": note["tags"],
            "created_at": note["created_at"],
            "updated_at": note["updated_at"],
            "content": note["content"],
        }
    )


@router.get("/api/workspaces/{name}/notes/{note_id}/chunks", response_model=ChunkPreviewResponse)
@logged_route
def api_get_note_chunks(
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
    preview = note_service.preview_chunks(note_id, owner_id=user["id"], ws_path=ws_path)
    if preview is None:
        return JSONResponse({"error": "Notatka nie istnieje."}, status_code=404)
    return JSONResponse(preview)


@router.get("/api/workspaces/{name}/notes/{note_id}/links", response_model=LinksResponse)
@logged_route
def api_note_links(
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
    result = note_service.links(note_id, owner_id=user["id"])
    if result is None:
        return JSONResponse({"error": "Notatka nie istnieje."}, status_code=404)
    return JSONResponse(result)


@router.get("/api/workspaces/{name}/notes/{note_id}/history", response_model=NoteHistoryResponse)
@logged_route
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
@logged_route
def api_note_version(
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
        version = note_service.get_version(note_id, sha, owner_id=user["id"], ws_path=ws_path)
    except (ValueError, GitError) as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse(
        {
            "note_id": version["note_id"],
            "title": version["title"],
            "folder": version["folder"],
            "tags": version["tags"],
            "created_at": version["created_at"],
            "updated_at": version["updated_at"],
            "content_html": _render_html(
                version["content"],
                resolver=note_service.link_resolver(name, user["id"]),
                slug=name,
            ),
        }
    )


@router.post(
    "/api/workspaces/{name}/notes/{note_id}/history/{sha}/restore",
    response_model=RestoreVersionResponse,
)
@logged_route
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
        result = await run_sync(
            note_service.restore_version, note_id, sha, owner_id=user["id"], ws_path=ws_path
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse(result)
