import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import (
    CreateFolderResponse,
    FolderMetaResponse,
    UpdateFolderMetaRequest,
)
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import get_folder_meta_repo, get_required_user, get_workspace_service
from kajet_turbo.errors import AuthError, FolderError
from kajet_turbo.log import logged_route, logger
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.repositories.git import GitError, GitRepository
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.workspace import normalize_folder

_FOLDER_PATH_RE = re.compile(r"^[a-zA-Z0-9._-][a-zA-Z0-9._\-/]*$")

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
@logged_route
async def api_create_folder(
    name: str,
    request: Request,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail=FolderError.PATH_REQUIRED) from None
    path = str(body.get("path", "")).strip().strip("/")
    if not path:
        raise HTTPException(status_code=422, detail=FolderError.PATH_REQUIRED)
    segments = path.split("/")
    if any(not s or s in (".", "..") for s in segments):
        raise HTTPException(status_code=422, detail=FolderError.PATH_INVALID)
    if not _FOLDER_PATH_RE.match(path):
        raise HTTPException(status_code=422, detail=FolderError.PATH_INVALID)
    ws_path = ws_service.workspace_path(user["id"], name)
    ws_root = Path(ws_path).resolve()
    target = (ws_root / path).resolve()
    try:
        target.relative_to(ws_root)
    except ValueError:
        raise HTTPException(status_code=422, detail=FolderError.PATH_INVALID) from None
    gitkeep = target / ".gitkeep"
    gitkeep.parent.mkdir(parents=True, exist_ok=True)
    if not gitkeep.exists():
        gitkeep.touch()
        relative = str(gitkeep.relative_to(ws_root))
        try:
            await run_sync(
                lambda: GitRepository(ws_path).commit_file(relative, f"folder: add {path}")
            )
        except GitError as e:
            gitkeep.unlink(missing_ok=True)
            raise HTTPException(
                status_code=500, detail={"error": "GIT_ERROR", "detail": str(e)}
            ) from e
    logger.info("folder_created", ws=name, path=path)
    return JSONResponse({"path": path})


@router.get(
    "/api/workspaces/{name}/folders/{path:path}/meta",
    response_model=FolderMetaResponse,
)
@logged_route
async def api_get_folder_meta(
    name: str,
    path: str,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    meta_repo: FolderMetaRepository = Depends(get_folder_meta_repo),
) -> FolderMetaResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    norm = normalize_folder(path)
    row = await run_sync(meta_repo.get, user["id"], name, norm)
    return FolderMetaResponse(
        path=norm,
        description=row.description if row else "",
        instructions=row.instructions if row else "",
    )


@router.put(
    "/api/workspaces/{name}/folders/{path:path}/meta",
    response_model=FolderMetaResponse,
)
@logged_route
async def api_update_folder_meta(
    name: str,
    path: str,
    body: UpdateFolderMetaRequest,
    user: dict = Depends(get_required_user),
    ws_service: WorkspaceService = Depends(get_workspace_service),
    meta_repo: FolderMetaRepository = Depends(get_folder_meta_repo),
) -> FolderMetaResponse:
    if not ws_service.has_access(user["id"], name):
        raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED)
    norm = normalize_folder(path)
    await run_sync(
        meta_repo.set,
        user["id"],
        name,
        norm,
        description=body.description,
        instructions=body.instructions,
    )
    return FolderMetaResponse(path=norm, description=body.description, instructions=body.instructions)
