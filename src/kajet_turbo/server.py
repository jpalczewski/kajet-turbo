import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastmcp.utilities.lifespan import combine_lifespans

from kajet_turbo.api import api_router
from kajet_turbo.auth import hash_password
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

    # RFC 8414 / RFC 9728: expose OAuth discovery routes at the origin root.
    # FastMCP generates path-aware well-known URLs for the issuer path (/mcp);
    # without this the SPA catch-all intercepts them and returns HTML.
    # mcp_path="/" matches http_app(path="/") above.
    for _route in provider.get_well_known_routes(mcp_path="/"):
        app.add_route(_route.path, _route.endpoint, methods=list(_route.methods) if _route.methods else ["GET"])

    # POST /mcp (no trailing slash) misses the /mcp mount and falls to the SPA → 405.
    @app.api_route("/mcp", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
    async def _mcp_slash_redirect(request: Request):
        return RedirectResponse(url="/mcp/", status_code=307)

    dist = Path(__file__).parent.parent.parent / "dist"
    if dist.exists():
        app.mount("/", _SPAFiles(str(dist)))

    return app


def main() -> None:
    import uvicorn
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))
    uvicorn.run(build_app(), host=host, port=port)
