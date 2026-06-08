import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastmcp.utilities.lifespan import combine_lifespans

from kajet_turbo.api import api_router
from kajet_turbo.auth import _resolve_base_url, hash_password
from kajet_turbo.dependencies import db, note_service, oauth_repo, provider, user_repo, workspace_service
from kajet_turbo.mcp import build_mcp


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    admin_email = os.getenv("KAJET_ADMIN_EMAIL")
    admin_password = os.getenv("KAJET_ADMIN_PASSWORD")
    if admin_email and admin_password and user_repo.count() == 0:
        user_repo.create(admin_email, hash_password(admin_password))
    try:
        yield
    finally:
        db.close()


class _SPAFiles:
    """Starlette mount that serves index.html for any path without a matching file (SPA fallback)."""

    def __init__(self, directory: str) -> None:
        from starlette.exceptions import HTTPException as StarletteHTTPException
        from starlette.staticfiles import StaticFiles

        class _SPA(StaticFiles):
            async def get_response(self, path: str, scope):
                try:
                    return await super().get_response(path, scope)
                except StarletteHTTPException as exc:
                    if exc.status_code == 404:
                        return await super().get_response("index.html", scope)
                    raise

        self._app = _SPA(directory=directory, html=True)

    async def __call__(self, scope, receive, send) -> None:
        await self._app(scope, receive, send)


def build_app() -> FastAPI:
    mcp = build_mcp(note_service, workspace_service, oauth_repo, provider)
    mcp_app = mcp.http_app(path="/")

    app = FastAPI(lifespan=combine_lifespans(_app_lifespan, mcp_app.lifespan))
    app.include_router(api_router)
    app.mount("/mcp", mcp_app)

    # RFC 8615: expose OAuth discovery at origin root so MCP clients find it
    # (clients look at {origin}/.well-known/... not {mcp_path}/.well-known/...)
    _mcp_base = _resolve_base_url().rstrip("/") + "/mcp"

    @app.get("/.well-known/oauth-authorization-server")
    async def _oauth_metadata_root():
        return JSONResponse({
            "issuer": _mcp_base,
            "authorization_endpoint": f"{_mcp_base}/authorize",
            "token_endpoint": f"{_mcp_base}/token",
            "registration_endpoint": f"{_mcp_base}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            "code_challenge_methods_supported": ["S256"],
        })

    dist = Path(__file__).parent.parent.parent / "dist"
    if dist.exists():
        app.mount("/", _SPAFiles(str(dist)))

    return app


def main() -> None:
    import uvicorn
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))
    uvicorn.run(build_app(), host=host, port=port)
