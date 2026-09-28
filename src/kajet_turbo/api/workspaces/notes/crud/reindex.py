from fastapi import APIRouter, Depends, HTTPException

from kajet_turbo.api.schemas import ReindexResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.dependencies import (
    RESOLVE_WORKSPACE_WRITE,
    get_note_reconcile_service,
)
from kajet_turbo.errors import NoteError
from kajet_turbo.services.notes import NoteReconcileService
from kajet_turbo.services.targets import WorkspaceTarget

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.post(
    "/api/workspaces/{name}/reindex",
    response_model=ReindexResponse,
    responses={409: {"model": ErrorResponse}},
)
def api_reindex_workspace(
    workspace: WorkspaceTarget = RESOLVE_WORKSPACE_WRITE,
    note_reconcile_service: NoteReconcileService = Depends(get_note_reconcile_service),
) -> ReindexResponse:
    try:
        result = note_reconcile_service.reindex(workspace)
    except ValueError as e:
        raise HTTPException(
            status_code=409,
            detail={"error": str(NoteError.RECONCILE_REFUSED), "detail": str(e)},
        ) from e
    return ReindexResponse(**result)
