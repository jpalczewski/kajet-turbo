import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastmcp.utilities.lifespan import combine_lifespans

from kajet_turbo.api import api_router
from kajet_turbo.auth import hash_password
from kajet_turbo.dependencies import (
    db,
    note_service,
    oauth_repo,
    provider,
    user_repo,
    workspace_service,
)
from kajet_turbo.log import LoggingMiddleware, logger, setup_logging
from kajet_turbo.mcp import build_mcp


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    gil_enabled = sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True
    logger.info("runtime", python=sys.version.split()[0], free_threading=not gil_enabled)
    admin_email = os.getenv("KAJET_ADMIN_EMAIL")
    admin_password = os.getenv("KAJET_ADMIN_PASSWORD")
    if admin_email and admin_password and user_repo.count() == 0:
        user_repo.create(admin_email, hash_password(admin_password))
    try:
        yield
    finally:
        db.close()


@asynccontextmanager
async def _logging_lifespan(app: FastAPI):
    # Must run AFTER mcp_app.lifespan: FastMCP's configure_logging() sets
    # propagate=False on the "fastmcp" stdlib logger and attaches a RichHandler.
    # setup_logging() replaces that handler with our _InterceptHandler so
    # FastMCP's internal messages flow through loguru and out as JSONL.
    setup_logging()
    yield


class _SPAFiles:
    """Starlette mount serving index.html for any path without a matching file (SPA fallback)."""

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


class _MCPPathFix:
    """Rewrite /mcp (no trailing slash) to /mcp/ at ASGI level to avoid 307 redirect.

    Starlette's Mount at /mcp only matches /mcp/... paths (not /mcp exactly).
    Without this fix the client receives a 307 redirect and some HTTP clients
    strip the Authorization header on redirect, causing 401 on the next request.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/mcp":
            scope = dict(scope)
            scope["path"] = "/mcp/"
        await self._app(scope, receive, send)


def build_app() -> Any:
    mcp = build_mcp(note_service, workspace_service, oauth_repo, provider)
    mcp_app = mcp.http_app(path="/")

    app = FastAPI(lifespan=combine_lifespans(_app_lifespan, mcp_app.lifespan, _logging_lifespan))
    app.add_middleware(LoggingMiddleware)
    app.include_router(api_router)
    app.mount("/mcp", mcp_app)

    # RFC 8414 / RFC 9728: expose OAuth discovery routes at the origin root.
    # FastMCP generates path-aware well-known URLs for the issuer path (/mcp);
    # without this the SPA catch-all intercepts them and returns HTML.
    for _route in provider.get_well_known_routes(mcp_path="/"):
        app.add_route(
            _route.path,
            _route.endpoint,
            methods=list(_route.methods) if _route.methods else ["GET"],
        )

    dist = Path(__file__).parent.parent.parent / "dist"
    if dist.exists():
        app.mount("/", _SPAFiles(str(dist)))

    return _MCPPathFix(app)


def main() -> None:
    import uvicorn

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))
    workers = int(os.getenv("MCP_WORKERS", "1"))
    uvicorn.run(
        "kajet_turbo.server:build_app",
        host=host,
        port=port,
        workers=workers,
        factory=True,
    )
