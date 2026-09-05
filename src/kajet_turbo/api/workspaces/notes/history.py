from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import NoteHistoryResponse, NoteHtmlResponse, RestoreVersionResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.api.workspaces.notes.content import _render_html
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import get_note_service, get_required_user, resolve_note_target
from kajet_turbo.errors import NoteError
from kajet_turbo.repositories.git import GitError as RepoGitError
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.targets import NoteTarget

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
def api_note_history(
    name: str,
    note_id: str,
    user: dict = Depends(get_required_user),
    target: NoteTarget = Depends(resolve_note_target),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    try:
        entries = note_service.get_history(target)
    except ValueError:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND) from None
    return JSONResponse({"entries": entries})


@router.get(
    "/api/workspaces/{name}/notes/{note_id}/history/{sha}",
    response_model=NoteHtmlResponse,
    responses={404: {"model": ErrorResponse}},
)
def api_note_version(
    name: str,
    note_id: str,
    sha: str,
    user: dict = Depends(get_required_user),
    target: NoteTarget = Depends(resolve_note_target),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    try:
        version = note_service.get_version(target, sha)
    except ValueError, RepoGitError:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND) from None
    return JSONResponse(
        {
            "note_id": version["note_id"],
            "title": version["title"],
            "folder": version["folder"],
            "tags": version["tags"],
            "created_at": version["created_at"],
            "updated_at": version["updated_at"],
            "occurred_at": version["occurred_at"],
            "period": version["period"],
            "content_html": _render_html(
                version["content"],
                resolver=note_service.link_resolver(name, user["id"], version["folder"]),
                slug=name,
                xws_resolver=note_service.xws_link_resolver(user["id"]),
            ),
        }
    )


@router.post(
    "/api/workspaces/{name}/notes/{note_id}/history/{sha}/restore",
    response_model=RestoreVersionResponse,
    responses={404: {"model": ErrorResponse}},
)
async def api_restore_note_version(
    name: str,
    note_id: str,
    sha: str,
    user: dict = Depends(get_required_user),
    target: NoteTarget = Depends(resolve_note_target),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    try:
        result = await run_sync(note_service.restore_version, target, sha)
    except ValueError:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND) from None
    return JSONResponse({"note_id": result["note_id"], "warnings": result["warnings"]})
