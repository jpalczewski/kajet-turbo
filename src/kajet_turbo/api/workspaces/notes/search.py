"""Hybrid search over one workspace (#453, W0 of epic #202).

Cross-workspace search (``workspace="all"``) stays an MCP feature: the web UI always
browses a single workspace, so the path names it and access is checked like any other
workspace route before the search runs.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.dependencies import (
    CurrentUser,
    get_note_search_service,
    get_required_user,
    resolve_workspace_target,
)
from kajet_turbo.services.notes import NoteSearchService
from kajet_turbo.services.notes.search import MAX_LIMIT, MIN_LIMIT
from kajet_turbo.services.targets import WorkspaceTarget
from kajet_turbo.shared.notes import SearchNotesResponse

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.get(
    "/api/workspaces/{name}/search",
    response_model=SearchNotesResponse,
    responses={422: {"model": ErrorResponse}},
)
async def api_search_notes(
    name: str,
    q: Annotated[str, Query(min_length=1, description="Search query")],
    folder: Annotated[
        str | None,
        Query(description="Restrict search to this folder and its descendants"),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Query(description="Restrict search to notes with any of these tags (hierarchical)"),
    ] = None,
    limit: Annotated[int, Query(ge=MIN_LIMIT, le=MAX_LIMIT)] = 10,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    search_service: NoteSearchService = Depends(get_note_search_service),
) -> SearchNotesResponse:
    # search_async manages its own run_sync slots (DB phases only), so it is awaited
    # directly. A bad folder raises InvalidFolderError, mapped to 422 by api/errors.py.
    outcome = await search_service.search_async(
        q,
        [workspace.name],
        owner_id=workspace.owner_id,
        limit=limit,
        folder=folder,
        tags=tags,
    )
    return SearchNotesResponse.model_validate(outcome, from_attributes=True)
