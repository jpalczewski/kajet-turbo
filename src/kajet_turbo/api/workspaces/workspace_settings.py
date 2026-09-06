from fastapi import APIRouter, Depends, HTTPException

from kajet_turbo import workspace_settings as ws_settings
from kajet_turbo.api.schemas import (
    ApplyTemporalBackfillRequest,
    ApplyTemporalBackfillResponse,
    SettingDefinition,
    TemporalBackfillPreviewResponse,
    UpdateWorkspaceSettingsRequest,
    UpdateWorkspaceSettingsResponse,
    WorkspaceSettingsResponse,
)
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import (
    CurrentUser,
    get_note_temporal_service,
    get_required_user,
    get_workspace_service,
    resolve_workspace_target,
)
from kajet_turbo.errors import WorkspaceError
from kajet_turbo.services.notes import NoteTemporalService
from kajet_turbo.services.notes.temporal import BackfillStaleError
from kajet_turbo.services.targets import WorkspaceTarget
from kajet_turbo.services.workspaces import WorkspaceService

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.get("/api/workspaces/{name}/settings", response_model=WorkspaceSettingsResponse)
async def api_get_workspace_settings(
    name: str,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceSettingsResponse:
    values = await run_sync(ws_service.get_settings, user.id, name)
    return WorkspaceSettingsResponse(
        definitions=[SettingDefinition(**d) for d in ws_settings.definitions()],
        values=values,
    )


@router.patch(
    "/api/workspaces/{name}/settings",
    response_model=UpdateWorkspaceSettingsResponse,
    responses={422: {"model": ErrorResponse}},
)
async def api_update_workspace_settings(
    name: str,
    body: UpdateWorkspaceSettingsRequest,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> UpdateWorkspaceSettingsResponse:
    # exclude_unset() -> only the setting keys the client actually sent are applied; a
    # setting the client didn't mention keeps its current value instead of being reset by
    # a default. UpdateWorkspaceSettingsValues.model_config's extra="forbid" already
    # rejected an unknown key and StrictBool already rejected a wrong-typed value before
    # this route ever runs, so set_setting's own ValueError here only covers a future
    # setting-specific validation rule (e.g. a cross-field constraint).
    updates = body.values.model_dump(exclude_unset=True)
    result: dict = {}
    try:
        for key, value in updates.items():
            result = await run_sync(ws_service.set_setting, user.id, name, key, value)
    except ValueError:
        raise HTTPException(status_code=422, detail=WorkspaceError.INVALID_INPUT) from None
    if not result:
        result = await run_sync(ws_service.get_settings, user.id, name)
    return UpdateWorkspaceSettingsResponse(values=result)


@router.post(
    "/api/workspaces/{name}/settings/temporal-backfill/preview",
    response_model=TemporalBackfillPreviewResponse,
)
async def api_temporal_backfill_preview(
    name: str,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    note_temporal_service: NoteTemporalService = Depends(get_note_temporal_service),
) -> TemporalBackfillPreviewResponse:
    result = await run_sync(
        note_temporal_service.temporal_backfill_preview,
        name,
        user.id,
        str(workspace.path),
    )
    return TemporalBackfillPreviewResponse(**result)


@router.post(
    "/api/workspaces/{name}/settings/temporal-backfill/apply",
    response_model=ApplyTemporalBackfillResponse,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def api_apply_temporal_backfill(
    name: str,
    body: ApplyTemporalBackfillRequest,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    note_temporal_service: NoteTemporalService = Depends(get_note_temporal_service),
) -> ApplyTemporalBackfillResponse:
    try:
        result = await run_sync(
            note_temporal_service.apply_temporal_backfill,
            name,
            user.id,
            str(workspace.path),
            [candidate.model_dump() for candidate in body.candidates],
        )
    except BackfillStaleError:
        raise HTTPException(status_code=409, detail=WorkspaceError.BACKFILL_STALE) from None
    except ValueError:
        raise HTTPException(status_code=422, detail=WorkspaceError.INVALID_INPUT) from None
    return ApplyTemporalBackfillResponse(**result)
