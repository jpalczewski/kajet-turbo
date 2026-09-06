from fastapi import APIRouter, Depends, HTTPException

from kajet_turbo.api.schemas import CreateSshKeyRequest, OkResponse, SshKeyItem, SshKeysResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import CurrentUser, get_required_user, get_ssh_key_service
from kajet_turbo.errors import SshKeyError
from kajet_turbo.repositories.ssh_keys import DuplicateKeyName
from kajet_turbo.services.ssh_keys import SshKeyService

router = APIRouter()


@router.get("/api/me/ssh-keys", response_model=SshKeysResponse)
def api_list_ssh_keys(
    user: CurrentUser = Depends(get_required_user),
    svc: SshKeyService = Depends(get_ssh_key_service),
) -> SshKeysResponse:
    return SshKeysResponse(keys=[SshKeyItem(**item) for item in svc.list_keys(user.id)])


@router.post(
    "/api/me/ssh-keys",
    status_code=201,
    response_model=SshKeyItem,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def api_create_ssh_key(
    body: CreateSshKeyRequest,
    user: CurrentUser = Depends(get_required_user),
    svc: SshKeyService = Depends(get_ssh_key_service),
) -> SshKeyItem:
    try:
        # Offload: keypair generation (RSA-4096 especially) is CPU-bound and would
        # block this route's event loop.
        result = await run_sync(svc.create_key, user.id, body.name, body.algorithm)
    except DuplicateKeyName:
        raise HTTPException(status_code=409, detail=SshKeyError.NAME_TAKEN) from None
    # The service never returns the private key (see SshKeyService._view) -- only public
    # material reaches this response, so there is nothing to redact here.
    return SshKeyItem(**result)


@router.delete(
    "/api/me/ssh-keys/{key_id}",
    response_model=OkResponse,
    responses={404: {"model": ErrorResponse}},
)
def api_delete_ssh_key(
    key_id: str,
    user: CurrentUser = Depends(get_required_user),
    svc: SshKeyService = Depends(get_ssh_key_service),
) -> OkResponse:
    if not svc.delete_key(user.id, key_id):
        raise HTTPException(status_code=404, detail=SshKeyError.NOT_FOUND)
    return OkResponse(ok=True)
