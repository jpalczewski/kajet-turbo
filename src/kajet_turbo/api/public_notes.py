from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from kajet_turbo.api.schemas import NoteHtmlResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.api.workspaces.notes.content import note_html_fields
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import (
    get_note_read_service,
    get_note_share_link_repo,
    get_workspace_service,
)
from kajet_turbo.errors import NoteError
from kajet_turbo.models import NoteShareLink
from kajet_turbo.repositories.note_share_link import NoteShareLinkRepository
from kajet_turbo.services.notes import NoteReadService
from kajet_turbo.services.notes.types import NoteData
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget
from kajet_turbo.services.workspaces import WorkspaceService

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store"}


def resolve_shared_note(
    token: str,
    share_link_repo: NoteShareLinkRepository,
    workspace_service: WorkspaceService,
    note_read_service: NoteReadService,
) -> tuple[NoteShareLink, NoteData] | None:
    """Resolve a share token to its link row and note content, or ``None`` if the token
    is unknown, revoked, or the note it pointed at is gone. Shared by the JSON public-note
    endpoint below and the server-rendered ``/shared/{token}`` preview route
    (``api/shared_preview.py``) -- both need the same path-resolution/git-read chain, but
    the preview route also needs the link itself (for ``preview_description``), which a
    fields-only return would have thrown away.

    Each caller records its own event kind after rendering succeeds; resolution alone
    must not count either a page view or a content read.
    """
    link = share_link_repo.resolve(token)
    if link is None:
        return None
    # The token is the sole authorization gate here -- workspace/note are trusted from the
    # resolved share-link row, never from has_access, unlike every other note read route.
    ws_path = workspace_service.workspace_path(link.owner_id, link.workspace)
    target = NoteTarget(
        note_id=link.note_id,
        workspace=WorkspaceTarget(owner_id=link.owner_id, name=link.workspace, path=Path(ws_path)),
    )
    note = note_read_service.get_with_content(target)
    if note is None:
        return None
    return link, note


def _load_public_note(
    token: str,
    ip: str | None,
    user_agent: str | None,
    share_link_repo: NoteShareLinkRepository,
    workspace_service: WorkspaceService,
    note_read_service: NoteReadService,
) -> dict | None:
    """Resolve a share token straight through to rendered fields in one thread dispatch --
    lookup, path computation, the git-backed read, and markdown rendering are all blocking
    calls chained on each other's output, so one run_sync() beats three."""
    resolved = resolve_shared_note(token, share_link_repo, workspace_service, note_read_service)
    if resolved is None:
        return None
    _link, note = resolved
    # No resolver/xws_resolver: render_markdown degrades wikilinks to a plain, unlinked
    # <span> instead of a real <a href> pointing at the note's folder/id -- the link text
    # itself (a note title) still renders, only the location it would otherwise expose does
    # not. See #348 for the full wikilink-leak scope this endpoint intentionally defers to.
    fields = note_html_fields(note)
    share_link_repo.record_visit(token, ip, user_agent)
    return fields


@router.get(
    "/api/public/notes/{token}",
    response_model=NoteHtmlResponse,
    responses={404: {"model": ErrorResponse}},
)
async def api_get_public_note(
    token: str,
    request: Request,
    response: Response,
    share_link_repo: NoteShareLinkRepository = Depends(get_note_share_link_repo),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
    note_read_service: NoteReadService = Depends(get_note_read_service),
) -> NoteHtmlResponse:
    # A revoked token must 404 on the very next request even through a caching proxy.
    # HTTPException(headers=...) below covers the 404 branch; this covers the 200 one.
    response.headers.update(_NO_STORE)
    ip = request.client.host if request.client is not None else None
    user_agent = request.headers.get("user-agent")
    fields = await run_sync(
        _load_public_note,
        token,
        ip,
        user_agent,
        share_link_repo,
        workspace_service,
        note_read_service,
    )
    if fields is None:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND, headers=_NO_STORE)
    return NoteHtmlResponse(**fields)
