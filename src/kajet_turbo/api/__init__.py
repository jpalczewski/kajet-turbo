from fastapi import APIRouter

from kajet_turbo.api.auth import router as auth_router
from kajet_turbo.api.embedding import router as embedding_router
from kajet_turbo.api.oauth import router as oauth_router
from kajet_turbo.api.ssh_keys import router as ssh_keys_router
from kajet_turbo.api.workspace_remote import router as workspace_remote_router
from kajet_turbo.api.workspaces import router as workspaces_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(oauth_router)
api_router.include_router(workspaces_router)
api_router.include_router(embedding_router)
api_router.include_router(ssh_keys_router)
api_router.include_router(workspace_remote_router)
