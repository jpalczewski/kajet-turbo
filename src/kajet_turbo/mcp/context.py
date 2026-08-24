import json
from dataclasses import dataclass
from datetime import timedelta

from fastmcp.dependencies import CurrentContext, Depends
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_access_token, get_context

from kajet_turbo import identity
from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logger
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.services.workspaces import WorkspaceService

# Global per-user fallback scope (ActiveWorkspaceRepository's default "user" scope).
# Bridges the claude.ai connector's per-tool-call session churn (it never echoes back
# Mcp-Session-Id, so mcp-session-scoped state can't survive to the next call) at the cost
# of a bounded window where two concurrent conversations of the same user can clobber
# each other's active workspace. TTL keeps that window small.
USER_SCOPE = "user"
USER_SCOPE_TTL = timedelta(hours=1)


@dataclass(frozen=True)
class ActiveWorkspace:
    owner_id: str
    name: str
    path: str


class McpContextDeps:
    workspace_service: WorkspaceService | None = None
    oauth_repo: OAuthRepository | None = None
    active_workspace_repo: ActiveWorkspaceRepository | None = None


deps = McpContextDeps()
MCP_CONTEXT = CurrentContext()


def configure_mcp_context(
    workspace_service: WorkspaceService,
    oauth_repo: OAuthRepository,
    active_workspace_repo: ActiveWorkspaceRepository,
) -> None:
    deps.workspace_service = workspace_service
    deps.oauth_repo = oauth_repo
    deps.active_workspace_repo = active_workspace_repo


def _resolve_user() -> str:
    """Sync identity resolver; run via run_sync at the MCP boundary."""
    token = get_access_token()
    if token is None:
        raise ToolError("Wymagane zalogowanie.")
    assert deps.oauth_repo is not None
    user_id = identity.user_id_for_client(deps.oauth_repo, token.client_id)
    if user_id is None:
        logger.warning("mcp_token_without_user", client_id=token.client_id)
        raise ToolError("Wymagane zalogowanie.")
    return user_id


async def require_user_id() -> str:
    return await run_sync(_resolve_user)


async def require_workspace_access(name: str, user_id: str) -> list[str]:
    assert deps.workspace_service is not None
    available = await run_sync(deps.workspace_service.list_accessible, user_id)
    if name in available:
        return available
    msg = f"Workspace '{name}' nie istnieje lub brak dostępu."
    raise ToolError(json.dumps({"error": msg, "available": available}))


async def _rehydrate_from_db(
    ctx: Context, user_id: str, db_name: str, *, source: str, scope: str
) -> ActiveWorkspace:
    assert deps.workspace_service is not None
    await ctx.set_state("active_workspace", db_name)
    await ctx.set_state("active_user_id", user_id)
    logger.info("active_workspace_resolved", source=source, ws=db_name, scope=scope)
    return ActiveWorkspace(
        owner_id=user_id,
        name=db_name,
        path=deps.workspace_service.workspace_path(user_id, db_name),
    )


async def active_workspace(ctx: Context = MCP_CONTEXT) -> ActiveWorkspace:
    """Resolve active workspace: session state, then the session-scoped DB row, then a
    time-boxed per-user DB row (see USER_SCOPE docstring above)."""
    assert deps.workspace_service is not None
    name = await ctx.get_state("active_workspace")
    if name:
        user_id: str = await ctx.get_state("active_user_id")
        logger.debug("active_workspace_resolved", source="session", ws=name)
        return ActiveWorkspace(
            owner_id=user_id,
            name=name,
            path=deps.workspace_service.workspace_path(user_id, name),
        )

    user_id = await require_user_id()
    assert deps.active_workspace_repo is not None
    scope = active_workspace_scope(ctx)
    if scope is not None:
        db_name = await run_sync(deps.active_workspace_repo.get, user_id, scope)
        if db_name:
            return await _rehydrate_from_db(
                ctx, user_id, db_name, source="session_db_fallback", scope=scope
            )

    db_name = await run_sync(deps.active_workspace_repo.get, user_id, USER_SCOPE, USER_SCOPE_TTL)
    if db_name:
        return await _rehydrate_from_db(
            ctx, user_id, db_name, source="user_scope_fallback", scope=USER_SCOPE
        )

    logger.info("active_workspace_miss")
    raise ToolError("Wywołaj activate_workspace() najpierw.")


def active_workspace_scope(ctx: Context) -> str | None:
    session_id = _context_session_id(ctx)
    if session_id:
        return f"mcp-session:{session_id}"
    return None


def _context_session_id(ctx: Context) -> str | None:
    try:
        return ctx.session_id
    except Exception:
        pass
    try:
        return get_context().session_id
    except Exception:
        return None


ACTIVE_WORKSPACE = Depends(active_workspace)
