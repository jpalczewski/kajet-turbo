import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastmcp.utilities.lifespan import combine_lifespans
from starlette.requests import Request as StarletteRequest

from kajet_turbo.api import api_router
from kajet_turbo.auth import hash_password
from kajet_turbo.dependencies import (
    active_workspace_repo,
    db,
    note_service,
    oauth_repo,
    provider,
    user_repo,
    workspace_service,
)
from kajet_turbo.log import LoggingMiddleware, logger, setup_logging
from kajet_turbo.mcp import build_mcp


def _make_sweep_handler(event_repo, job_repo):
    def _sweep(payload: dict) -> None:
        swept = event_repo.sweep(3600.0)
        logger.info("outbox_sweep", swept=swept)
        job_repo.enqueue("sweep_outbox", {}, dedup_key="sweep_outbox", delay=900.0)

    return _sweep


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


def _new_mcp_app() -> Any:
    mcp = build_mcp(note_service, workspace_service, oauth_repo, active_workspace_repo, provider)
    return mcp.http_app(path="/")


def _add_oauth_routes(app: FastAPI) -> None:
    # RFC 8414 / RFC 9728: expose OAuth discovery routes at the origin root.
    # FastMCP generates path-aware well-known URLs for the issuer path (/mcp);
    # without this the SPA catch-all intercepts them and returns HTML.
    for _route in provider.get_well_known_routes(mcp_path="/"):
        app.add_route(
            _route.path,
            _route.endpoint,
            methods=list(_route.methods) if _route.methods else ["GET"],
        )


def _mount_spa(app: FastAPI) -> None:
    dist = Path(__file__).parent.parent.parent / "dist"
    if dist.exists():
        app.mount("/", _SPAFiles(str(dist)))


async def _http_exception_handler(request: StarletteRequest, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


def build_mcp_app() -> Any:
    """MCP role: /mcp + OAuth routes only. Stateful — must run single-process."""
    mcp_app = _new_mcp_app()
    app = FastAPI(lifespan=combine_lifespans(_app_lifespan, mcp_app.lifespan, _logging_lifespan))
    app.add_middleware(LoggingMiddleware)
    app.mount("/mcp", mcp_app)
    _add_oauth_routes(app)
    return _MCPPathFix(app)


def build_api_app() -> Any:
    """API role: REST /api + SPA. Stateless — scales to any worker count."""
    app = FastAPI(lifespan=combine_lifespans(_app_lifespan, _logging_lifespan))
    app.add_exception_handler(HTTPException, _http_exception_handler)  # ty: ignore[invalid-argument-type] — FastAPI accepts narrower exc type at runtime
    app.add_middleware(LoggingMiddleware)
    app.include_router(api_router)
    _mount_spa(app)
    return app


def build_app() -> Any:
    """Combined role ("all"): MCP + API + SPA in one process (local dev)."""
    mcp_app = _new_mcp_app()
    app = FastAPI(lifespan=combine_lifespans(_app_lifespan, mcp_app.lifespan, _logging_lifespan))
    app.add_exception_handler(HTTPException, _http_exception_handler)  # ty: ignore[invalid-argument-type] — FastAPI accepts narrower exc type at runtime
    app.add_middleware(LoggingMiddleware)
    app.include_router(api_router)
    app.mount("/mcp", mcp_app)
    _add_oauth_routes(app)
    _mount_spa(app)
    return _MCPPathFix(app)


def main() -> None:
    import uvicorn

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))
    role = os.getenv("KAJET_ROLE", "all")
    if role == "worker":
        from kajet_turbo.db import Database
        from kajet_turbo.dependencies import heal_handler, push_handler
        from kajet_turbo.worker import register_handler, run_worker

        # The worker returns before any uvicorn app is built, so it must init logging
        # itself — otherwise it falls back to loguru's default human sink (no `extra`
        # fields), and push errors never reach the logs. This switches it to the JSON
        # sink, which includes the bound fields (workspace, error, ...).
        setup_logging()

        if os.getenv("KAJET_MIGRATE_BRANCHES_ON_START", "1") == "1":
            # Bring legacy `master` workspaces onto `main`. Idempotent — a pure
            # no-op scan once converged. Disable via KAJET_MIGRATE_BRANCHES_ON_START=0
            # when done (issue #15). A migration failure must not block startup.
            from kajet_turbo.log import logger
            from kajet_turbo.maintenance import migrate_workspaces_to_main
            from kajet_turbo.workspace import WORKSPACES_DIR

            try:
                migrated = migrate_workspaces_to_main(WORKSPACES_DIR)
                logger.info("startup_branch_migration", migrated=len(migrated))
            except Exception as e:
                logger.warning("startup_branch_migration_failed", error=str(e))

        register_handler("push_workspace", push_handler)
        register_handler("heal_dangling", heal_handler)
        from kajet_turbo.dependencies import event_repo
        from kajet_turbo.dependencies import job_repo as _job_repo

        register_handler("sweep_outbox", _make_sweep_handler(event_repo, _job_repo))
        _job_repo.enqueue("sweep_outbox", {}, dedup_key="sweep_outbox")
        db = Database()
        run_worker(
            db.engine,
            poll_interval=float(os.getenv("KAJET_WORKER_POLL_INTERVAL", "1")),
            concurrency=int(os.getenv("KAJET_WORKER_CONCURRENCY", "4")),
        )
        return
    if role == "mcp":
        # Hard invariant: stateful MCP sessions live in process memory, so the
        # MCP role MUST be single-process regardless of any env. This is the fix.
        factory, workers = "kajet_turbo.server:build_mcp_app", 1
    elif role == "api":
        factory = "kajet_turbo.server:build_api_app"
        workers = int(os.getenv("API_WORKERS", "2"))
    else:
        factory = "kajet_turbo.server:build_app"
        workers = int(os.getenv("MCP_WORKERS", "1"))
    uvicorn.run(factory, host=host, port=port, workers=workers, factory=True)
