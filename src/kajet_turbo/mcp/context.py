import json
from dataclasses import dataclass

from fastmcp.dependencies import CurrentContext, Depends
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_access_token

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logger
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.services.workspaces import WorkspaceService


@dataclass(frozen=True)
class ActiveWorkspace:
    owner_id: str
    name: str
    path: str
    user_id: str | None


class McpContextDeps:
    workspace_service: WorkspaceService | None = None
    oauth_repo: OAuthRepository | None = None
    active_workspace_repo: ActiveWorkspaceRepository | None = None


deps = McpContextDeps()
MCP_CONTEXT = CurrentContext()
LEGACY_ACTIVE_WORKSPACE_SCOPE = "user"


def configure_mcp_context(
    workspace_service: WorkspaceService,
    oauth_repo: OAuthRepository,
    active_workspace_repo: ActiveWorkspaceRepository,
) -> None:
    deps.workspace_service = workspace_service
    deps.oauth_repo = oauth_repo
    deps.active_workspace_repo = active_workspace_repo


def resolve_user() -> str | None:
    """Sync identity resolver; run via run_sync at the MCP boundary."""
    token = get_access_token()
    if token is None:
        return None
    assert deps.oauth_repo is not None
    user_id = deps.oauth_repo.get_user_id_by_client(token.client_id)
    if user_id is None:
        raise ToolError("unauthorized")
    return user_id


async def resolve_user_id() -> str | None:
    return await run_sync(resolve_user)


async def require_user_id() -> str:
    user_id = await resolve_user_id()
    if user_id is None:
        raise ToolError("Wymagane zalogowanie.")
    return user_id


async def require_workspace_access(name: str, user_id: str | None) -> list[str]:
    assert deps.workspace_service is not None
    available = await run_sync(deps.workspace_service.list_accessible, user_id)
    if name in available:
        return available
    msg = (
        "Workspace '{name}' nie istnieje lub brak dostępu."
        if user_id
        else "Workspace '{name}' nie istnieje."
    )
    raise ToolError(json.dumps({"error": msg.format(name=name), "available": available}))


async def active_workspace(ctx: Context = MCP_CONTEXT) -> ActiveWorkspace:
    """Resolve active workspace from session state or the per-user DB fallback."""
    assert deps.workspace_service is not None
    name = await ctx.get_state("active_workspace")
    if name:
        owner_id: str = await ctx.get_state("active_owner_id")
        user_id: str | None = await ctx.get_state("active_user_id")
        logger.debug("active_workspace_resolved", source="session", ws=name)
        return ActiveWorkspace(
            owner_id=owner_id,
            name=name,
            path=deps.workspace_service.workspace_path(user_id, name),
            user_id=user_id,
        )

    user_id = await resolve_user_id()
    if user_id is not None and deps.active_workspace_repo is not None:
        scope = active_workspace_scope(ctx)
        db_name = await run_sync(deps.active_workspace_repo.get, user_id, scope)
        if db_name:
            await ctx.set_state("active_workspace", db_name)
            await ctx.set_state("active_user_id", user_id)
            await ctx.set_state("active_owner_id", user_id)
            logger.info(
                "active_workspace_resolved",
                source="db_fallback",
                ws=db_name,
                scope=scope,
            )
            return ActiveWorkspace(
                owner_id=user_id,
                name=db_name,
                path=deps.workspace_service.workspace_path(user_id, db_name),
                user_id=user_id,
            )

    logger.info("active_workspace_miss", authenticated=user_id is not None)
    raise ToolError("Wywołaj activate_workspace() najpierw.")


async def get_active_workspace(ctx: Context) -> tuple[str, str, str]:
    ws = await active_workspace(ctx)
    return ws.owner_id, ws.name, ws.path


def active_workspace_scope(ctx: Context) -> str:
    session_id = getattr(ctx, "session_id", None)
    if session_id:
        return f"mcp-session:{session_id}"
    return LEGACY_ACTIVE_WORKSPACE_SCOPE


ACTIVE_WORKSPACE = Depends(active_workspace)
