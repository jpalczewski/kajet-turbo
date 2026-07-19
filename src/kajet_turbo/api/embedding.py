from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import EmbeddingProfilesResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import get_embedding_profile_service, get_required_user
from kajet_turbo.log import logged_route
from kajet_turbo.services.embedding_profiles import EmbeddingProfileService

router = APIRouter()


@router.get("/api/me/embedding-profiles", response_model=EmbeddingProfilesResponse)
@logged_route
def api_list_embedding_profiles(
    user: dict = Depends(get_required_user),
    svc: EmbeddingProfileService = Depends(get_embedding_profile_service),
) -> JSONResponse:
    return JSONResponse({"profiles": svc.list_profiles(user["id"])})


@router.post("/api/me/embedding-profiles")
@logged_route
async def api_create_embedding_profile(
    request: Request,
    user: dict = Depends(get_required_user),
    svc: EmbeddingProfileService = Depends(get_embedding_profile_service),
) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    try:
        # Offload to a worker thread: create_profile runs a probe embed via asyncio.run,
        # which cannot be called from this async route's running event loop.
        result = await run_sync(
            svc.create_profile,
            user["id"],
            name=body.get("name", ""),
            base_url=body.get("base_url", ""),
            model=body.get("model", ""),
            api_key=body.get("api_key"),
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(result, status_code=201)


@router.put("/api/me/embedding-profiles/{profile_id}")
@logged_route
async def api_update_embedding_profile(
    profile_id: str,
    request: Request,
    user: dict = Depends(get_required_user),
    svc: EmbeddingProfileService = Depends(get_embedding_profile_service),
) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    try:
        # Offload to a worker thread (probe embed uses asyncio.run — see create above).
        result = await run_sync(
            svc.update_profile,
            user["id"],
            profile_id,
            name=body.get("name", ""),
            base_url=body.get("base_url", ""),
            model=body.get("model", ""),
            api_key=body.get("api_key"),
        )
    except ValueError as e:
        msg = str(e)
        status = 404 if "not found" in msg else 400
        return JSONResponse({"error": msg}, status_code=status)
    return JSONResponse(result)


@router.post("/api/me/embedding-profiles/{profile_id}/activate")
@logged_route
def api_activate_embedding_profile(
    profile_id: str,
    user: dict = Depends(get_required_user),
    svc: EmbeddingProfileService = Depends(get_embedding_profile_service),
) -> JSONResponse:
    try:
        svc.activate_profile(user["id"], profile_id)
    except ValueError:
        return JSONResponse({"error": "Profil nie istnieje."}, status_code=404)
    return JSONResponse({"ok": True})


@router.delete("/api/me/embedding-profiles/{profile_id}")
@logged_route
def api_delete_embedding_profile(
    profile_id: str,
    user: dict = Depends(get_required_user),
    svc: EmbeddingProfileService = Depends(get_embedding_profile_service),
) -> JSONResponse:
    svc.delete_profile(user["id"], profile_id)
    return JSONResponse({"ok": True})
