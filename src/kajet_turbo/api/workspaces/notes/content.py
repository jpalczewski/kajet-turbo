import bleach
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import (
    ChunkPreviewResponse,
    LinksResponse,
    NoteHtmlResponse,
    NoteMarkdownResponse,
)
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.dependencies import get_note_service, get_required_user, get_workspace_service
from kajet_turbo.errors import AuthError, NoteError
from kajet_turbo.log import logged_route, logger
from kajet_turbo.markdown import LinkResolver, render_markdown
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService

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


router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.get(
    "/api/workspaces/{name}/notes/{note_id}/html",
    response_model=NoteHtmlResponse,
    responses={404: {"model": ErrorResponse}},
)
@logged_route
def api_get_note_html(
    name: str,
    note_id: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        logger.warning(
            "note_html_access_denied",
            user_id=user["id"],
            email=user.get("email"),
            workspace=name,
            note_id=note_id,
        )
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    ws_path = ws_service.workspace_path(user["id"], name)
    note = note_service.get_with_content(note_id, owner_id=user["id"], ws_path=ws_path)
    if note is None:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND)
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


@router.get(
    "/api/workspaces/{name}/notes/{note_id}/markdown",
    response_model=NoteMarkdownResponse,
    responses={404: {"model": ErrorResponse}},
)
@logged_route
def api_get_note_markdown(
    name: str,
    note_id: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    ws_path = ws_service.workspace_path(user["id"], name)
    note = note_service.get_with_content(note_id, owner_id=user["id"], ws_path=ws_path)
    if note is None:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND)
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


@router.get(
    "/api/workspaces/{name}/notes/{note_id}/chunks",
    response_model=ChunkPreviewResponse,
    responses={404: {"model": ErrorResponse}},
)
@logged_route
def api_get_note_chunks(
    name: str,
    note_id: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    ws_path = ws_service.workspace_path(user["id"], name)
    preview = note_service.preview_chunks(note_id, owner_id=user["id"], ws_path=ws_path)
    if preview is None:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND)
    return JSONResponse(preview)


@router.get(
    "/api/workspaces/{name}/notes/{note_id}/links",
    response_model=LinksResponse,
    responses={404: {"model": ErrorResponse}},
)
@logged_route
def api_note_links(
    name: str,
    note_id: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    result = note_service.links(note_id, owner_id=user["id"])
    if result is None:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND)
    return JSONResponse(result)
