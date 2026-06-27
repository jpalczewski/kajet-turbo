import json

from fastmcp import Context
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logger
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.services.workspaces import WorkspaceService


class Deps:
    """Repos configured by build_workspaces for cross-module workspace context."""

    oauth_repo: OAuthRepository | None = None
    active_workspace_repo: ActiveWorkspaceRepository | None = None


deps = Deps()


def configure_context(
    oauth_repo: OAuthRepository,
    active_workspace_repo: ActiveWorkspaceRepository,
) -> None:
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


async def require_workspace_access(
    workspace_service: WorkspaceService,
    user_id: str | None,
    name: str,
) -> list[str]:
    available = await run_sync(workspace_service.list_accessible, user_id)
    if name in available:
        return available
    msg = (
        "Workspace '{name}' nie istnieje lub brak dostępu."
        if user_id
        else "Workspace '{name}' nie istnieje."
    )
    raise ToolError(json.dumps({"error": msg.format(name=name), "available": available}))


async def get_active_workspace(
    ctx: Context, workspace_service: WorkspaceService
) -> tuple[str, str, str]:
    """Returns (owner_id, workspace_slug, workspace_path).

    Session state is the fast path. When it is empty, fall back to the DB
    per-user store keyed by the authenticated user.
    """
    name = await ctx.get_state("active_workspace")
    if name:
        owner_id: str = await ctx.get_state("active_owner_id")
        real_user_id: str | None = await ctx.get_state("active_user_id")
        logger.debug("active_workspace_resolved", source="session", ws=name)
        return owner_id, name, workspace_service.workspace_path(real_user_id, name)

    user_id = await resolve_user_id()
    if user_id is not None and deps.active_workspace_repo is not None:
        db_name = await run_sync(deps.active_workspace_repo.get, user_id)
        if db_name:
            await ctx.set_state("active_workspace", db_name)
            await ctx.set_state("active_user_id", user_id)
            await ctx.set_state("active_owner_id", user_id)
            logger.info("active_workspace_resolved", source="db_fallback", ws=db_name)
            return user_id, db_name, workspace_service.workspace_path(user_id, db_name)

    logger.info("active_workspace_miss", authenticated=user_id is not None)
    raise RuntimeError("Wywołaj activate_workspace() najpierw.")
