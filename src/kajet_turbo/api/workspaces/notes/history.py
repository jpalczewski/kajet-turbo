from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import NoteHistoryResponse, NoteHtmlResponse, RestoreVersionResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.api.workspaces.notes.content import _render_html
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import get_note_service, get_required_user, get_workspace_service
from kajet_turbo.errors import AuthError, NoteError
from kajet_turbo.log import logged_route
from kajet_turbo.repositories.git import GitError as RepoGitError
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.get(
    "/api/workspaces/{name}/notes/{note_id}/history",
    response_model=NoteHistoryResponse,
    responses={404: {"model": ErrorResponse}},
)
@logged_route
def api_note_history(
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
        entries = note_service.get_history(note_id, owner_id=user["id"], ws_path=ws_path)
    except ValueError:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND) from None
    return JSONResponse({"entries": entries})


@router.get(
    "/api/workspaces/{name}/notes/{note_id}/history/{sha}",
    response_model=NoteHtmlResponse,
    responses={404: {"model": ErrorResponse}},
)
@logged_route
def api_note_version(
    name: str,
    note_id: str,
    sha: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    ws_path = ws_service.workspace_path(user["id"], name)
    try:
        version = note_service.get_version(note_id, sha, owner_id=user["id"], ws_path=ws_path)
    except (ValueError, RepoGitError):
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND) from None
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
    responses={404: {"model": ErrorResponse}},
)
@logged_route
async def api_restore_note_version(
    name: str,
    note_id: str,
    sha: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    ws_path = ws_service.workspace_path(user["id"], name)
    try:
        result = await run_sync(
            note_service.restore_version, note_id, sha, owner_id=user["id"], ws_path=ws_path
        )
    except ValueError:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND) from None
    return JSONResponse(result)
