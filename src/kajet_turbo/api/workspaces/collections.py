from fastapi import APIRouter, Depends, HTTPException

from kajet_turbo.api.schemas import CollectionEntriesResponse, CollectionsListResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.collections import collection_result_payload
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import (
    CurrentUser,
    get_collection_service,
    get_required_user,
    resolve_workspace_target,
)
from kajet_turbo.errors import CollectionError
from kajet_turbo.services.collections import CollectionService
from kajet_turbo.services.targets import WorkspaceTarget
from kajet_turbo.shared.collections import CollectionResult

router = APIRouter(responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}})


@router.get(
    "/api/workspaces/{name}/collections",
    response_model=CollectionsListResponse,
)
async def api_list_collections(
    name: str,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    collection_service: CollectionService = Depends(get_collection_service),
) -> CollectionsListResponse:
    definitions = await run_sync(collection_service.list_collections, str(workspace.path))
    return CollectionsListResponse(
        collections=[
            CollectionResult.model_validate(collection_result_payload(collection_name, definition))
            for collection_name, definition in definitions.items()
        ]
    )


@router.get(
    "/api/workspaces/{name}/collections/{collection}/entries",
    response_model=CollectionEntriesResponse,
    responses={404: {"model": ErrorResponse}},
)
async def api_list_collection_entries(
    name: str,
    collection: str,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    collection_service: CollectionService = Depends(get_collection_service),
) -> CollectionEntriesResponse:
    try:
        entries = await run_sync(
            collection_service.list_entries,
            str(workspace.path),
            workspace.name,
            workspace.owner_id,
            collection,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=CollectionError.NOT_FOUND) from exc
    return CollectionEntriesResponse(notes=entries)
