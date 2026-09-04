from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import UserPreferences
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import get_preferences_service, get_required_user
from kajet_turbo.errors import PreferencesError
from kajet_turbo.services.preferences import PreferencesService

router = APIRouter(responses={401: {"model": ErrorResponse}})


@router.get("/api/me/preferences", response_model=UserPreferences)
def api_get_preferences(
    user: dict = Depends(get_required_user),
    svc: PreferencesService = Depends(get_preferences_service),
) -> JSONResponse:
    return JSONResponse(svc.get_preferences(user["id"]).model_dump())


@router.patch(
    "/api/me/preferences",
    response_model=UserPreferences,
    responses={422: {"model": ErrorResponse}},
)
async def api_update_preferences(
    request: Request,
    user: dict = Depends(get_required_user),
    svc: PreferencesService = Depends(get_preferences_service),
) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail=PreferencesError.INVALID_INPUT) from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail=PreferencesError.INVALID_INPUT)

    updates: dict[str, str] = {}
    for key in ("timezone", "locale"):
        if key in body:  # `in`, not `.get() is not None` — explicit null must 422, not no-op
            value = body[key]
            if not isinstance(value, str):
                raise HTTPException(status_code=422, detail=PreferencesError.INVALID_INPUT)
            updates[key] = value

    try:
        prefs = await run_sync(svc.update_preferences, user["id"], **updates)
    except ValueError:
        raise HTTPException(status_code=422, detail=PreferencesError.INVALID_INPUT) from None
    return JSONResponse(prefs.model_dump())
