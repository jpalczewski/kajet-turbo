from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import SshKeysResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import get_session_user, get_ssh_key_service
from kajet_turbo.log import logged_route
from kajet_turbo.repositories.ssh_keys import DuplicateKeyName
from kajet_turbo.services.ssh_keys import SshKeyService

router = APIRouter()


@router.get("/api/me/ssh-keys", response_model=SshKeysResponse)
@logged_route
def api_list_ssh_keys(
    request: Request,
    svc: SshKeyService = Depends(get_ssh_key_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    return JSONResponse({"keys": svc.list_keys(user["id"])})


@router.post("/api/me/ssh-keys")
@logged_route
async def api_create_ssh_key(
    request: Request,
    svc: SshKeyService = Depends(get_ssh_key_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    try:
        # Offload: keypair generation (RSA-4096 especially) is CPU-bound and would
        # block this route's event loop.
        result = await run_sync(
            svc.create_key,
            user["id"],
            body.get("name", ""),
            body.get("algorithm", ""),
        )
    except DuplicateKeyName as e:
        return JSONResponse({"error": f"A key named '{e}' already exists"}, status_code=409)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(result, status_code=201)


@router.delete("/api/me/ssh-keys/{key_id}")
@logged_route
def api_delete_ssh_key(
    key_id: str,
    request: Request,
    svc: SshKeyService = Depends(get_ssh_key_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not svc.delete_key(user["id"], key_id):
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({"ok": True})
