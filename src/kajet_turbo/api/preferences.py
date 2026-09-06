from fastapi import APIRouter, Depends, HTTPException

from kajet_turbo.api.schemas import UpdatePreferencesRequest, UserPreferences
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import CurrentUser, get_preferences_service, get_required_user
from kajet_turbo.errors import PreferencesError
from kajet_turbo.services.preferences import PreferencesService

router = APIRouter(responses={401: {"model": ErrorResponse}})


@router.get("/api/me/preferences", response_model=UserPreferences)
def api_get_preferences(
    user: CurrentUser = Depends(get_required_user),
    svc: PreferencesService = Depends(get_preferences_service),
) -> UserPreferences:
    return svc.get_preferences(user.id)


@router.patch(
    "/api/me/preferences",
    response_model=UserPreferences,
    responses={422: {"model": ErrorResponse}},
)
async def api_update_preferences(
    body: UpdatePreferencesRequest,
    user: CurrentUser = Depends(get_required_user),
    svc: PreferencesService = Depends(get_preferences_service),
) -> UserPreferences:
    # exclude_unset (Pydantic's own model_fields_set-based mechanism, matching the
    # workspace_meta.py/workspace_settings.py PATCH routes) is what tells "field omitted"
    # (no-op) apart from "field present but null" (must 422, not silently no-op) -- both
    # parse to the same `None` attribute once Pydantic has validated the body.
    sent = body.model_dump(exclude_unset=True)
    if any(value is None for value in sent.values()):
        raise HTTPException(status_code=422, detail=PreferencesError.INVALID_INPUT)
    updates: dict[str, str] = sent

    try:
        prefs = await run_sync(svc.update_preferences, user.id, **updates)
    except ValueError:
        raise HTTPException(status_code=422, detail=PreferencesError.INVALID_INPUT) from None
    return prefs
