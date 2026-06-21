from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from kajet_turbo.api.schemas import EmbeddingBackendsResponse, EmbeddingConfigResponse
from kajet_turbo.dependencies import get_embedding_config_service, get_session_user
from kajet_turbo.log import logged_route
from kajet_turbo.services.embedding_config import EmbeddingConfigService

router = APIRouter()


@router.get("/api/embedding/backends", response_model=EmbeddingBackendsResponse)
@logged_route
def api_embedding_backends(
    request: Request,
    svc: EmbeddingConfigService = Depends(get_embedding_config_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    return JSONResponse(svc.list_backends(user["id"]))


@router.put("/api/me/embedding-config", response_model=EmbeddingConfigResponse)
@logged_route
async def api_set_embedding_config(
    request: Request,
    svc: EmbeddingConfigService = Depends(get_embedding_config_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    try:
        result = svc.set_config(
            user["id"], backend_id=body.get("backend_id"), api_key=body.get("api_key")
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(result)
