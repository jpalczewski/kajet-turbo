import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastmcp.utilities.lifespan import combine_lifespans
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request as StarletteRequest

from kajet_turbo.api import api_router
from kajet_turbo.auth import hash_password
from kajet_turbo.dependencies import AppConfig, AppResources, build_resources
from kajet_turbo.health import add_health_routes
from kajet_turbo.log import LoggingMiddleware, install_loop_exception_handler, logger, setup_logging
from kajet_turbo.mcp import build_mcp
from kajet_turbo.repositories.git import use_post_commit_hooks

_SPA_EXPLORER_PATH = re.compile(r"^workspace/[A-Za-z0-9][A-Za-z0-9_-]{0,49}/notes(?:/.*)?$")


def _make_sweep_handler(event_repo, job_repo):
    def _sweep(payload: dict) -> None:
        swept = event_repo.sweep(3600.0)
        purged = job_repo.sweep_done(86400.0)
        # jobs_purged is normally 1 (this sweep job's own predecessor from 24h ago);
        # only log at INFO when something beyond that steady state happened, so a
        # quiet system doesn't emit a zeroed line every 15 minutes.
        level = "info" if swept or purged > 1 else "debug"
        logger.log(level.upper(), "outbox_sweep", swept=swept, jobs_purged=purged)
        job_repo.enqueue("sweep_outbox", {}, dedup_key="sweep_outbox", delay=900.0)

    return _sweep


def register_job_handlers(resources: AppResources) -> dict[str, Any]:
    """Register every job kind the worker can run. Shared by the standalone worker
    role and the combined app's in-process worker thread."""
    handlers = {
        "push_workspace": resources.push_handler,
        "reconcile_links": resources.reconcile_links_handler,
        # Drain jobs written before deployment with the new idempotent implementation.
        "heal_dangling": resources.reconcile_links_handler,
        "sweep_outbox": _make_sweep_handler(resources.event_repo, resources.job_repo),
        "embed_note": resources.embed_handler,
        "reindex_note": resources.reindex_handler,
    }
    return handlers


@asynccontextmanager
async def _app_lifespan(app: FastAPI):
    resources: AppResources = app.state.resources
    config = resources.config
    if config.admin_email and config.admin_password and resources.user_repo.count() == 0:
        resources.user_repo.create(config.admin_email, hash_password(config.admin_password))
    try:
        yield
    finally:
        await resources.aclose()


@asynccontextmanager
async def _logging_lifespan(app: FastAPI):
    # Must run AFTER mcp_app.lifespan: FastMCP's configure_logging() sets
    # propagate=False on the "fastmcp" stdlib logger and attaches a RichHandler.
    # setup_logging() replaces that handler with our _InterceptHandler so
    # FastMCP's internal messages flow through loguru and out as JSONL.
    setup_logging()
    install_loop_exception_handler()
    gil_enabled = sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True
    logger.info("runtime", python=sys.version.split()[0], free_threading=not gil_enabled)
    yield


@asynccontextmanager
async def _sweep_outbox_lifespan(app: FastAPI):
    app.state.resources.job_repo.enqueue("sweep_outbox", {}, dedup_key="sweep_outbox")
    yield


@asynccontextmanager
async def _worker_lifespan(app: FastAPI):
    # Role "all" has no separate worker process, so drain the job queue in-process —
    # otherwise deferred embeddings and auto-push silently never run in bare local dev.
    import threading

    from kajet_turbo.worker import run_worker

    resources: AppResources = app.state.resources
    stop = threading.Event()

    def run() -> None:
        with use_post_commit_hooks(resources.post_commit_hooks):
            run_worker(
                resources.db.engine,
                registry=register_job_handlers(resources),
                poll_interval=resources.config.worker_poll_interval,
                concurrency=resources.config.worker_concurrency,
                stop_event=stop,
            )

    thread = threading.Thread(
        target=run,
        daemon=True,
        name="kajet-inprocess-worker",
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        # Unconditional: run_worker's ThreadPoolExecutor already drains in-flight jobs
        # before returning, so a timed join would gain nothing but let _app_lifespan
        # close the engine under a still-running job (see push_handler/git_push, whose
        # unbounded SSH push is the actual hang risk — bound that, not this join).
        thread.join()


def _is_spa_navigation(path: str, scope: dict) -> bool:
    """Whether a missing path is a browser navigation eligible for the SPA shell.

    A root-mounted static app must not turn scanner probes into successful responses.
    The explorer route is the one exception to the extension rule: user folder names may
    contain dots (for example ``2026.08``), while hidden path segments stay ineligible.
    """
    if scope["method"] not in {"GET", "HEAD"} or "text/html" not in Headers(scope=scope).get(
        "accept", ""
    ):
        return False
    segments = [segment for segment in path.split("/") if segment]
    if any(segment.startswith(".") for segment in segments):
        return False
    if _SPA_EXPLORER_PATH.fullmatch(path):
        return True
    final_segment = segments[-1] if segments else ""
    return not final_segment or "." not in final_segment


class _SPAFiles:
    """Serve static files and the SPA shell only for eligible browser navigations."""

    def __init__(self, directory: str) -> None:
        from starlette.staticfiles import StaticFiles

        class _SPA(StaticFiles):
            async def get_response(self, path: str, scope):
                if scope["method"] not in {"GET", "HEAD"}:
                    raise StarletteHTTPException(status_code=404)
                try:
                    return await super().get_response(path, scope)
                except StarletteHTTPException as exc:
                    if exc.status_code == 404 and _is_spa_navigation(path, scope):
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

    @property
    def state(self):
        """Expose ``state.resources.aclose()`` for an app assembled but never started."""
        return self._app.state

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and scope.get("path") == "/mcp":
            scope = dict(scope)
            scope["path"] = "/mcp/"
        await self._app(scope, receive, send)


class _ResourceHookScope:
    """Bind post-commit callbacks to this ASGI app without a process-global list."""

    def __init__(self, app: Any, resources: AppResources) -> None:
        self._app = app
        self._resources = resources

    async def __call__(self, scope, receive, send) -> None:
        with use_post_commit_hooks(self._resources.post_commit_hooks):
            await self._app(scope, receive, send)


def _new_mcp_app(resources: AppResources) -> Any:
    mcp = build_mcp(resources)
    return mcp.http_app(path="/")


def _add_oauth_routes(app: FastAPI, resources: AppResources) -> None:
    # RFC 8414 / RFC 9728: expose OAuth discovery routes at the origin root.
    # FastMCP generates path-aware well-known URLs for the issuer path (/mcp);
    # without this the SPA catch-all intercepts them and returns HTML.
    for _route in resources.provider.get_well_known_routes(mcp_path="/"):
        app.add_route(
            _route.path,
            _route.endpoint,
            methods=list(_route.methods) if _route.methods else ["GET"],
        )


def _mount_spa(app: FastAPI, resources: AppResources) -> None:
    dist = Path(__file__).parent.parent.parent / "dist"
    if resources.config.serve_spa and dist.exists():
        app.mount("/", _SPAFiles(str(dist)))


async def _http_exception_handler(request: StarletteRequest, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


def _assemble(config: AppConfig | None) -> AppResources:
    return build_resources(config or AppConfig.from_env())


def build_mcp_app(config: AppConfig | None = None) -> Any:
    """MCP role: /mcp + OAuth routes only. Stateful — must run single-process."""
    resources = _assemble(config)
    try:
        mcp_app = _new_mcp_app(resources)
        app = FastAPI(
            lifespan=combine_lifespans(_app_lifespan, mcp_app.lifespan, _logging_lifespan)
        )
        app.state.resources = resources
    except BaseException:
        resources.db.close()
        raise
    app.add_middleware(LoggingMiddleware, resources=resources)
    app.add_middleware(_ResourceHookScope, resources=resources)
    add_health_routes(app, engine=resources.db.engine)
    app.mount("/mcp", mcp_app)
    _add_oauth_routes(app, resources)
    return _MCPPathFix(app)


def build_api_app(config: AppConfig | None = None) -> Any:
    """API role: REST /api + SPA. Stateless — scales to any worker count."""
    resources = _assemble(config)
    try:
        app = FastAPI(lifespan=combine_lifespans(_app_lifespan, _logging_lifespan))
        app.state.resources = resources
    except BaseException:
        resources.db.close()
        raise
    app.add_exception_handler(HTTPException, _http_exception_handler)  # ty: ignore[invalid-argument-type] — FastAPI accepts narrower exc type at runtime
    app.add_middleware(LoggingMiddleware, resources=resources)
    app.add_middleware(_ResourceHookScope, resources=resources)
    add_health_routes(app, engine=resources.db.engine)
    app.include_router(api_router)
    _mount_spa(app, resources)
    return app


def build_app(config: AppConfig | None = None) -> Any:
    """Combined role ("all"): MCP + API + SPA in one process (local dev)."""
    resources = _assemble(config)
    try:
        mcp_app = _new_mcp_app(resources)
        app = FastAPI(
            lifespan=combine_lifespans(
                _app_lifespan,
                mcp_app.lifespan,
                _logging_lifespan,
                _sweep_outbox_lifespan,
                _worker_lifespan,
            )
        )
        app.state.resources = resources
    except BaseException:
        resources.db.close()
        raise
    app.add_exception_handler(HTTPException, _http_exception_handler)  # ty: ignore[invalid-argument-type] — FastAPI accepts narrower exc type at runtime
    app.add_middleware(LoggingMiddleware, resources=resources)
    app.add_middleware(_ResourceHookScope, resources=resources)
    add_health_routes(app, engine=resources.db.engine)
    app.include_router(api_router)
    app.mount("/mcp", mcp_app)
    _add_oauth_routes(app, resources)
    _mount_spa(app, resources)
    return _MCPPathFix(app)


_TECH_USER_DOMAIN = "@kajet.local"


def _cmd_create_user(args: list[str]) -> None:
    import argparse
    import secrets

    parser = argparse.ArgumentParser(prog="kajet-turbo create-user")
    parser.add_argument(
        "--email",
        default=f"stress_{secrets.token_hex(4)}{_TECH_USER_DOMAIN}",
        help="Email for the technical user (default: auto-generated @kajet.local address)",
    )
    parsed = parser.parse_args(args)

    password = secrets.token_urlsafe(16)
    resources = build_resources(AppConfig.from_env())
    try:
        resources.user_repo.create(parsed.email, hash_password(password))
    finally:
        resources.db.close()
    print(f"email:    {parsed.email}")
    print(f"password: {password}")


def _cmd_purge_tech_users() -> None:
    from sqlalchemy import text

    from kajet_turbo.db import Database

    database = Database()
    try:
        engine = database.engine
        pattern = f"%{_TECH_USER_DOMAIN}"

        # Must delete child rows first — FKs have no CASCADE.
        child_tables = [
            "sessions",
            "client_authorizations",
            "active_workspaces",
            "workspace_access",
            "workspace_meta",
            "embedding_profiles",
            "workspace_remotes",
            "ssh_keys",
            "jobs",
        ]
        with engine.connect() as conn:
            ids = [
                row[0]
                for row in conn.execute(
                    text("SELECT id FROM users WHERE email LIKE :p"), {"p": pattern}
                ).fetchall()
            ]
            if not ids:
                print("No technical users found.")
                return
            placeholders = ", ".join(f":id{i}" for i in range(len(ids)))
            params = {f"id{i}": uid for i, uid in enumerate(ids)}
            for table in child_tables:
                conn.execute(
                    text(f"DELETE FROM {table} WHERE user_id IN ({placeholders})"),
                    params,
                )
            conn.execute(
                text(f"DELETE FROM users WHERE id IN ({placeholders})"),
                params,
            )
            conn.commit()

        import shutil

        from kajet_turbo.workspace import WORKSPACES_DIR

        ws_base = Path(WORKSPACES_DIR)
        removed_dirs: list[str] = []
        for uid in ids:
            user_dir = ws_base / uid
            if user_dir.is_dir():
                shutil.rmtree(user_dir)
                removed_dirs.append(str(user_dir))
    finally:
        database.close()
    print(f"Purged {len(ids)} technical user(s): {', '.join(ids)}")
    if removed_dirs:
        print(f"Removed workspace dirs: {', '.join(removed_dirs)}")


def main() -> None:
    import uvicorn

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "create-user":
            _cmd_create_user(sys.argv[2:])
            return
        if cmd == "purge-tech-users":
            _cmd_purge_tech_users()
            return

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))
    role = os.getenv("KAJET_ROLE", "all")
    if role == "worker":
        from kajet_turbo.worker import run_worker

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

        resources = build_resources(AppConfig.from_env())
        try:
            resources.job_repo.enqueue("sweep_outbox", {}, dedup_key="sweep_outbox")
            with use_post_commit_hooks(resources.post_commit_hooks):
                run_worker(
                    resources.db.engine,
                    registry=register_job_handlers(resources),
                    poll_interval=resources.config.worker_poll_interval,
                    concurrency=resources.config.worker_concurrency,
                )
        finally:
            resources.db.close()
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
