from fastapi import APIRouter, Depends, HTTPException

from kajet_turbo.api.schemas import (
    CreateEmbeddingProfileRequest,
    EmbeddingProfileItem,
    EmbeddingProfilesResponse,
    OkResponse,
    UpdateEmbeddingProfileRequest,
)
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import CurrentUser, get_embedding_profile_service, get_required_user
from kajet_turbo.errors import EmbeddingProfileError
from kajet_turbo.repositories.embedding_profiles import ProfileNotFoundError
from kajet_turbo.services.embedding_profiles import EmbeddingProfileService

router = APIRouter()


@router.get("/api/me/embedding-profiles", response_model=EmbeddingProfilesResponse)
def api_list_embedding_profiles(
    user: CurrentUser = Depends(get_required_user),
    svc: EmbeddingProfileService = Depends(get_embedding_profile_service),
) -> EmbeddingProfilesResponse:
    return EmbeddingProfilesResponse(
        profiles=[EmbeddingProfileItem(**item) for item in svc.list_profiles(user.id)]
    )


@router.post(
    "/api/me/embedding-profiles",
    status_code=201,
    response_model=EmbeddingProfileItem,
    responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def api_create_embedding_profile(
    body: CreateEmbeddingProfileRequest,
    user: CurrentUser = Depends(get_required_user),
    svc: EmbeddingProfileService = Depends(get_embedding_profile_service),
) -> EmbeddingProfileItem:
    try:
        # Offload to a worker thread: create_profile runs a probe embed via asyncio.run,
        # which cannot be called from this async route's running event loop.
        result = await run_sync(
            svc.create_profile,
            user.id,
            name=body.name,
            base_url=body.base_url,
            model=body.model,
            api_key=body.api_key,
        )
    except ValueError:
        # Only the probe (connectivity/shape) can fail here -- there is no profile yet to
        # be "not found". api_key is never included in the ValueError message (see
        # EmbeddingProfileService._probe_dim), so nothing secret reaches this response.
        raise HTTPException(status_code=400, detail=EmbeddingProfileError.PROBE_FAILED) from None
    return EmbeddingProfileItem(**result)


@router.put(
    "/api/me/embedding-profiles/{profile_id}",
    response_model=EmbeddingProfileItem,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def api_update_embedding_profile(
    profile_id: str,
    body: UpdateEmbeddingProfileRequest,
    user: CurrentUser = Depends(get_required_user),
    svc: EmbeddingProfileService = Depends(get_embedding_profile_service),
) -> EmbeddingProfileItem:
    try:
        # Offload to a worker thread (probe embed uses asyncio.run — see create above).
        result = await run_sync(
            svc.update_profile,
            user.id,
            profile_id,
            name=body.name,
            base_url=body.base_url,
            model=body.model,
            api_key=body.api_key,
        )
    except ProfileNotFoundError:
        raise HTTPException(status_code=404, detail=EmbeddingProfileError.NOT_FOUND) from None
    except ValueError:
        raise HTTPException(status_code=400, detail=EmbeddingProfileError.PROBE_FAILED) from None
    return EmbeddingProfileItem(**result)


@router.post(
    "/api/me/embedding-profiles/{profile_id}/activate",
    response_model=OkResponse,
    responses={404: {"model": ErrorResponse}},
)
def api_activate_embedding_profile(
    profile_id: str,
    user: CurrentUser = Depends(get_required_user),
    svc: EmbeddingProfileService = Depends(get_embedding_profile_service),
) -> OkResponse:
    try:
        svc.activate_profile(user.id, profile_id)
    except ProfileNotFoundError:
        raise HTTPException(status_code=404, detail=EmbeddingProfileError.NOT_FOUND) from None
    return OkResponse(ok=True)


@router.delete("/api/me/embedding-profiles/{profile_id}", response_model=OkResponse)
def api_delete_embedding_profile(
    profile_id: str,
    user: CurrentUser = Depends(get_required_user),
    svc: EmbeddingProfileService = Depends(get_embedding_profile_service),
) -> OkResponse:
    svc.delete_profile(user.id, profile_id)
    return OkResponse(ok=True)
