from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse

from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import get_required_user, get_workspace_service
from kajet_turbo.errors import AuthError
from kajet_turbo.repositories.git import GitError
from kajet_turbo.services.workspace_export import WorkspaceExportService
from kajet_turbo.services.workspaces import WorkspaceService

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    }
)

_exports = WorkspaceExportService()


@router.get("/api/workspaces/{name}/export")
async def api_export_workspace(
    name: str,
    background_tasks: BackgroundTasks,
    format: Literal["zip", "tar.zst", "bundle"] = "zip",
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> FileResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    try:
        ws_path = ws_service.workspace_path(user["id"], name)
        export = await run_sync(_exports.create, name, ws_path, format)
    except GitError as e:
        raise HTTPException(status_code=500, detail={"error": "GIT_ERROR", "detail": str(e)}) from e
    background_tasks.add_task(export.path.unlink, missing_ok=True)
    return FileResponse(export.path, media_type=export.media_type, filename=export.filename)
