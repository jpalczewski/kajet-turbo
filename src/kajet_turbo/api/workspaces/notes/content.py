from typing import Annotated

import bleach
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import (
    ChunkPreviewResponse,
    GraphResponse,
    LinksResponse,
    NoteHtmlResponse,
    NoteMarkdownResponse,
)
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.dependencies import (
    CurrentUser,
    get_note_link_service,
    get_note_read_service,
    get_required_user,
    resolve_note_target,
    resolve_workspace_target,
)
from kajet_turbo.errors import NoteError
from kajet_turbo.markdown import LinkResolver, XwsResolver, render_markdown
from kajet_turbo.services.notes import NoteLinkService, NoteReadService
from kajet_turbo.services.notes.types import NoteData
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget

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
    content: str,
    resolver: LinkResolver | None = None,
    slug: str | None = None,
    xws_resolver: XwsResolver | None = None,
) -> str:
    return bleach.clean(
        render_markdown(content, resolver, slug, xws_resolver),
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRS,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )


def note_html_fields(
    note: NoteData,
    resolver: LinkResolver | None = None,
    slug: str | None = None,
    xws_resolver: XwsResolver | None = None,
) -> dict:
    return {
        "note_id": note.note_id,
        "title": note.title,
        "folder": note.folder,
        "tags": note.tags,
        "created_at": note.created_at,
        "updated_at": note.updated_at,
        "occurred_at": note.occurred_at,
        "period": note.period,
        "extras": note.extras,
        "content_html": _render_html(
            note.content, resolver=resolver, slug=slug, xws_resolver=xws_resolver
        ),
        "sha": note.sha,
    }


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
def api_get_note_html(
    name: str,
    note_id: str,
    user: CurrentUser = Depends(get_required_user),
    target: NoteTarget = Depends(resolve_note_target),
    note_read_service: NoteReadService = Depends(get_note_read_service),
    link_service: NoteLinkService = Depends(get_note_link_service),
) -> JSONResponse:
    note = note_read_service.get_with_content(target)
    if note is None:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND)
    return JSONResponse(
        note_html_fields(
            note,
            resolver=link_service.link_resolver(target.workspace, note.folder),
            slug=target.workspace.name,
            xws_resolver=link_service.xws_link_resolver(target.workspace.owner_id),
        )
    )


@router.get(
    "/api/workspaces/{name}/notes/{note_id}/markdown",
    response_model=NoteMarkdownResponse,
    responses={404: {"model": ErrorResponse}},
)
def api_get_note_markdown(
    name: str,
    note_id: str,
    user: CurrentUser = Depends(get_required_user),
    target: NoteTarget = Depends(resolve_note_target),
    note_read_service: NoteReadService = Depends(get_note_read_service),
) -> JSONResponse:
    note = note_read_service.get_with_content(target)
    if note is None:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND)
    return JSONResponse(
        {
            "note_id": note.note_id,
            "title": note.title,
            "folder": note.folder,
            "tags": note.tags,
            "created_at": note.created_at,
            "updated_at": note.updated_at,
            "occurred_at": note.occurred_at,
            "period": note.period,
            "extras": note.extras,
            "content": note.content,
            "sha": note.sha,
        }
    )


@router.get(
    "/api/workspaces/{name}/notes/{note_id}/chunks",
    response_model=ChunkPreviewResponse,
    responses={404: {"model": ErrorResponse}},
)
def api_get_note_chunks(
    name: str,
    note_id: str,
    user: CurrentUser = Depends(get_required_user),
    target: NoteTarget = Depends(resolve_note_target),
    note_read_service: NoteReadService = Depends(get_note_read_service),
) -> JSONResponse:
    preview = note_read_service.preview_chunks(
        target.note_id,
        owner_id=target.workspace.owner_id,
        ws_path=str(target.workspace.path),
    )
    if preview is None:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND)
    return JSONResponse(preview)


@router.get(
    "/api/workspaces/{name}/notes/{note_id}/links",
    response_model=LinksResponse,
    responses={404: {"model": ErrorResponse}},
)
def api_note_links(
    name: str,
    note_id: str,
    user: CurrentUser = Depends(get_required_user),
    target: NoteTarget = Depends(resolve_note_target),
    link_service: NoteLinkService = Depends(get_note_link_service),
) -> JSONResponse:
    result = link_service.links(target)
    if result is None:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND)
    return JSONResponse(result)


@router.get(
    "/api/workspaces/{name}/notes/{note_id}/neighborhood",
    response_model=GraphResponse,
    responses={404: {"model": ErrorResponse}},
)
def api_note_neighborhood(
    name: str,
    note_id: str,
    depth: Annotated[int, Query(ge=1, le=3)] = 2,
    include_cross_workspace: bool = False,
    include_tags: bool = False,
    user: CurrentUser = Depends(get_required_user),
    target: NoteTarget = Depends(resolve_note_target),
    link_service: NoteLinkService = Depends(get_note_link_service),
) -> JSONResponse:
    result = link_service.neighborhood(
        target,
        depth,
        include_cross_workspace,
        include_tags,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND)
    return JSONResponse(result)


@router.get(
    "/api/workspaces/{name}/notes/graph",
    response_model=GraphResponse,
)
def api_note_graph(
    name: str,
    include_tags: bool = False,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    link_service: NoteLinkService = Depends(get_note_link_service),
) -> JSONResponse:
    return JSONResponse(link_service.graph(workspace, include_tags=include_tags))
