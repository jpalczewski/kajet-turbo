from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import ConsentResponse, PendingInfoResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import get_provider, get_session_user

router = APIRouter()


@router.post("/api/consent", response_model=ConsentResponse)
async def api_consent(
    request: Request,
    provider=Depends(get_provider),
) -> Response:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    pending_id = str(body.get("pending_id", ""))
    if not pending_id:
        return JSONResponse({"error": "Wygasły pending_id."}, status_code=400)
    try:
        redirect_uri = await provider.complete_authorization(pending_id, user["id"])
    except ValueError:
        return JSONResponse({"error": "Wygasły pending_id."}, status_code=400)
    return JSONResponse({"redirect_uri": redirect_uri})


@router.get("/api/pending", response_model=PendingInfoResponse)
async def api_pending_info(
    id: str = Query(...),
    provider=Depends(get_provider),
) -> Response:
    client = await run_sync(provider.get_pending_client, id)
    if client is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    name = getattr(client, "client_name", None) or client.client_id
    return JSONResponse({"client_name": name})
