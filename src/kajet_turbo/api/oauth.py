from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from kajet_turbo.dependencies import get_provider, get_session_user

router = APIRouter()


@router.post("/api/consent")
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
    if not pending_id or pending_id not in provider._pending:
        return JSONResponse({"error": "Wygasły pending_id."}, status_code=400)
    try:
        redirect_uri = await provider.complete_authorization(pending_id, user["id"])
    except ValueError:
        return JSONResponse({"error": "Wygasły pending_id."}, status_code=400)
    return JSONResponse({"redirect_uri": redirect_uri})


@router.get("/api/pending")
async def api_pending_info(
    request: Request,
    provider=Depends(get_provider),
) -> Response:
    pending_id = request.query_params.get("id", "")
    if not pending_id or pending_id not in provider._pending:
        return JSONResponse({"error": "Not found"}, status_code=404)
    client, _ = provider._pending[pending_id]
    name = getattr(client, "client_name", None) or client.client_id
    return JSONResponse({"client_name": name})
