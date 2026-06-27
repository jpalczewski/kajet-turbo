from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import (
    BatchCreateNotesResponse,
    CreateNoteResponse,
    DeleteNoteResponse,
    MoveNoteResponse,
    NotesListResponse,
    UpdateNoteResponse,
)
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.api.workspaces.notes._views import enrich_note_items
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import get_note_service, get_required_user, get_workspace_service
from kajet_turbo.errors import AuthError, FolderError, NoteError
from kajet_turbo.log import logged_route
from kajet_turbo.markdown import BrokenWikilinkError
from kajet_turbo.repositories.git import GitError  # exception class, not errors.GitError StrEnum
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.workspace import InvalidFolderError

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.get(
    "/api/workspaces/{name}/notes",
    response_model=NotesListResponse,
)
@logged_route
def api_list_notes(
    name: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
    folder: str | None = None,
    tag: str | None = None,
    include_descendants: bool = True,
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    ws_path = ws_service.workspace_path(user["id"], name)
    if tag is not None:
        notes = note_service.notes_by_tag(
            name, user["id"], tag, include_descendants=include_descendants
        )
    else:
        notes = note_service.list_notes(name, owner_id=user["id"], folder=folder, limit=None)
    return JSONResponse({"notes": enrich_note_items(ws_path, notes)})


@router.post(
    "/api/workspaces/{name}/notes",
    status_code=201,
    response_model=CreateNoteResponse,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
@logged_route
async def api_create_note(
    name: str,
    request: Request,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail=NoteError.INVALID_INPUT) from None
    title = str(body.get("title", "")).strip()
    if not title:
        raise HTTPException(status_code=422, detail=NoteError.TITLE_REQUIRED)
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
        raise HTTPException(
            status_code=422,
            detail={"error": str(NoteError.BROKEN_WIKILINK), "detail": str(e)},
        ) from e
    except ValueError:
        raise HTTPException(status_code=409, detail=NoteError.ALREADY_EXISTS) from None
    except Exception as e:
        raise HTTPException(
            status_code=500, detail={"error": str(NoteError.INVALID_INPUT), "detail": str(e)}
        ) from e
    return JSONResponse(result, status_code=201)


@router.post(
    "/api/workspaces/{name}/notes/batch",
    response_model=BatchCreateNotesResponse,
    responses={422: {"model": ErrorResponse}},
)
@logged_route
async def api_create_notes_batch(
    name: str,
    request: Request,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail=NoteError.INVALID_INPUT) from None
    notes = body.get("notes")
    if not isinstance(notes, list) or not notes:
        raise HTTPException(status_code=422, detail=NoteError.INVALID_INPUT)
    ws_path = ws_service.workspace_path(user["id"], name)
    try:
        results = await run_sync(note_service.save_many, user["id"], name, ws_path, notes)
    except GitError as e:
        raise HTTPException(
            status_code=500, detail={"error": str(NoteError.INVALID_INPUT), "detail": str(e)}
        ) from e
    return JSONResponse({"results": results}, status_code=200)


@router.patch(
    "/api/workspaces/{name}/notes/{note_id}",
    response_model=UpdateNoteResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
@logged_route
async def api_update_note(
    name: str,
    note_id: str,
    request: Request,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail=NoteError.INVALID_INPUT) from None
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
            confirm=True,
        )
    except InvalidFolderError:
        raise HTTPException(status_code=422, detail=FolderError.INVALID_FOLDER) from None
    except BrokenWikilinkError as e:
        raise HTTPException(
            status_code=422,
            detail={"error": str(NoteError.BROKEN_WIKILINK), "detail": str(e)},
        ) from e
    except FileExistsError:
        raise HTTPException(status_code=409, detail=NoteError.ALREADY_EXISTS) from None
    except ValueError, FileNotFoundError:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND) from None
    return JSONResponse(result)


@router.post(
    "/api/workspaces/{name}/notes/{note_id}/move",
    response_model=MoveNoteResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
@logged_route
async def api_move_note(
    name: str,
    note_id: str,
    request: Request,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail=NoteError.INVALID_INPUT) from None
    folder = body.get("folder")
    if not isinstance(folder, str):
        raise HTTPException(status_code=422, detail=FolderError.PATH_REQUIRED)
    ws_path = ws_service.workspace_path(user["id"], name)
    try:
        result = await run_sync(
            note_service.move,
            note_id,
            owner_id=user["id"],
            ws_path=ws_path,
            folder=folder,
        )
    except InvalidFolderError:
        raise HTTPException(status_code=422, detail=FolderError.INVALID_FOLDER) from None
    except ValueError, FileNotFoundError:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND) from None
    except FileExistsError:
        raise HTTPException(status_code=409, detail=NoteError.ALREADY_EXISTS) from None
    except Exception as e:
        raise HTTPException(
            status_code=500, detail={"error": str(NoteError.INVALID_INPUT), "detail": str(e)}
        ) from e
    return JSONResponse(result)


@router.delete(
    "/api/workspaces/{name}/notes/{note_id}",
    response_model=DeleteNoteResponse,
    responses={404: {"model": ErrorResponse}},
)
@logged_route
async def api_delete_note(
    name: str,
    note_id: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    ws_path = ws_service.workspace_path(user["id"], name)
    try:
        await run_sync(note_service.delete, note_id, owner_id=user["id"], ws_path=ws_path)
    except ValueError:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND) from None
    except Exception as e:
        raise HTTPException(
            status_code=500, detail={"error": str(NoteError.INVALID_INPUT), "detail": str(e)}
        ) from e
    return JSONResponse({"ok": True})
