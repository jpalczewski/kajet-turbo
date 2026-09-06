from fastapi import APIRouter, Depends

from kajet_turbo.api.schemas import (
    CreateFolderRequest,
    CreateFolderResponse,
    FolderMetaResponse,
    UpdateFolderMetaRequest,
)
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import (
    CurrentUser,
    get_folder_meta_repo,
    get_note_folder_service,
    get_required_user,
    resolve_workspace_target,
)
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.services.notes import NoteFolderService
from kajet_turbo.services.targets import WorkspaceTarget
from kajet_turbo.workspace import normalize_folder

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.post(
    "/api/workspaces/{name}/folders",
    response_model=CreateFolderResponse,
    responses={422: {"model": ErrorResponse}},
)
async def api_create_folder(
    name: str,
    body: CreateFolderRequest,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    folder_service: NoteFolderService = Depends(get_note_folder_service),
) -> CreateFolderResponse:
    path = await run_sync(folder_service.create_folder, workspace, body.path)
    return CreateFolderResponse(path=path)


@router.get(
    "/api/workspaces/{name}/folders/{path:path}/meta",
    response_model=FolderMetaResponse,
)
async def api_get_folder_meta(
    name: str,
    path: str,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    meta_repo: FolderMetaRepository = Depends(get_folder_meta_repo),
) -> FolderMetaResponse:
    norm = normalize_folder(path)
    row = await run_sync(meta_repo.get, user.id, name, norm)
    return FolderMetaResponse(
        path=norm,
        description=row.description if row else "",
        instructions=row.instructions if row else "",
    )


@router.put(
    "/api/workspaces/{name}/folders/{path:path}/meta",
    response_model=FolderMetaResponse,
)
async def api_update_folder_meta(
    name: str,
    path: str,
    body: UpdateFolderMetaRequest,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    meta_repo: FolderMetaRepository = Depends(get_folder_meta_repo),
) -> FolderMetaResponse:
    norm = normalize_folder(path)
    await run_sync(
        meta_repo.set,
        user.id,
        name,
        norm,
        description=body.description,
        instructions=body.instructions,
    )
    return FolderMetaResponse(
        path=norm, description=body.description, instructions=body.instructions
    )
