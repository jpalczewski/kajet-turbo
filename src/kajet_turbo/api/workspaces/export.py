from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import FileResponse

from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import CurrentUser, get_required_user, resolve_workspace_target
from kajet_turbo.services.targets import WorkspaceTarget
from kajet_turbo.services.workspace_export import WorkspaceExportService

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
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
) -> FileResponse:
    # Exempt from response_model / the JSON envelope (#254) -- this is a file download, not
    # JSON (see the exemption list in tests/api/test_endpoint_contract.py). A local
    # `except GitError` used to live here and shadow api/errors.py's global GitError
    # handler with a version that leaked str(e) into the 500 body; removed rather than
    # migrated -- let the global handler map it instead.
    export = await run_sync(_exports.create, name, str(workspace.path), format)
    background_tasks.add_task(export.path.unlink, missing_ok=True)
    return FileResponse(export.path, media_type=export.media_type, filename=export.filename)
