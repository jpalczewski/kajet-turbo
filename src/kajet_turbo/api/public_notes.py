from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response

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
from kajet_turbo.repositories.note_share_link import NoteShareLinkRepository
from kajet_turbo.services.notes import NoteReadService
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget
from kajet_turbo.services.workspaces import WorkspaceService

router = APIRouter()


@router.get(
    "/api/public/notes/{token}",
    response_model=NoteHtmlResponse,
    responses={404: {"model": ErrorResponse}},
)
async def api_get_public_note(
    token: str,
    response: Response,
    share_link_repo: NoteShareLinkRepository = Depends(get_note_share_link_repo),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
    note_read_service: NoteReadService = Depends(get_note_read_service),
) -> NoteHtmlResponse:
    # A revoked token must 404 on the very next request even through a caching proxy.
    response.headers["Cache-Control"] = "no-store"
    link = await run_sync(share_link_repo.resolve, token)
    if link is None:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND)
    # The token is the sole authorization gate here -- workspace/note are trusted from the
    # resolved share-link row, never from has_access, unlike every other note read route.
    ws_path = await run_sync(workspace_service.workspace_path, link.owner_id, link.workspace)
    target = NoteTarget(
        note_id=link.note_id,
        workspace=WorkspaceTarget(owner_id=link.owner_id, name=link.workspace, path=Path(ws_path)),
    )
    note = await run_sync(note_read_service.get_with_content, target)
    if note is None:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND)
    # No resolver/xws_resolver: render_markdown degrades wikilinks to plain text instead of
    # a real <a href>, so an anonymous viewer never learns a linked private note exists.
    return NoteHtmlResponse(**note_html_fields(note))
