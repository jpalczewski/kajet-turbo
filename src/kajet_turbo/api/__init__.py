from fastapi import APIRouter

from kajet_turbo.api.auth import router as auth_router
from kajet_turbo.api.oauth import router as oauth_router
from kajet_turbo.api.workspaces import router as workspaces_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(oauth_router)
api_router.include_router(workspaces_router)
