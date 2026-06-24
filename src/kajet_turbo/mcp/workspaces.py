import json

from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import get_access_token
from nanoid import generate

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool, logger
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.services.workspaces import WorkspaceService


class _Deps:
    """Holder for repos set by register_workspaces, so the module-level
    get_active_workspace (called from notes.py) can resolve identity and reach the
    per-user store without threading new params through 15+ call sites."""

    oauth_repo: OAuthRepository | None = None
    active_workspace_repo: ActiveWorkspaceRepository | None = None


_deps = _Deps()


def _resolve_user() -> tuple[str | None, str | None]:
    """Sync — run via run_sync. Returns (user_id, err_json).

    user_id is the stable User.id (None when unauthenticated). err_json is set
    only when a token is present but maps to no user.
    """
    token = get_access_token()
    if token is None:
        return None, None
    assert _deps.oauth_repo is not None
    user_id = _deps.oauth_repo.get_user_id_by_client(token.client_id)
    if user_id is None:
        return None, json.dumps({"error": "unauthorized"})
    return user_id, None


async def get_active_workspace(
    ctx: Context, workspace_service: WorkspaceService
) -> tuple[str, str, str]:
    """Returns (owner_id, workspace_slug, workspace_path).

    Session state is the fast path (single-session clients like Claude Code, plus
    sampling/elicitation). When it's empty — which is every tool call on the
    claude.ai connector, since it opens a fresh MCP session per call — fall back
    to the DB per-user store keyed by the authenticated user. Only raise when both
    miss.
    """
    name = await ctx.get_state("active_workspace")
    if name:
        owner_id: str = await ctx.get_state("active_owner_id")
        real_user_id: str | None = await ctx.get_state("active_user_id")
        logger.debug("active_workspace_resolved", source="session", ws=name)
        return owner_id, name, workspace_service.workspace_path(real_user_id, name)

    user_id, _ = await run_sync(_resolve_user)
    if user_id is not None and _deps.active_workspace_repo is not None:
        db_name = await run_sync(_deps.active_workspace_repo.get, user_id)
        if db_name:
            # Rehydrate session state so downstream reads within THIS call see a
            # consistent identity (e.g. search_notes reads active_user_id directly).
            await ctx.set_state("active_workspace", db_name)
            await ctx.set_state("active_user_id", user_id)
            await ctx.set_state("active_owner_id", user_id)
            # Signals the connector opened a fresh session (no in-session state) but
            # we recovered via the per-user store. A high rate here = session churn.
            logger.info("active_workspace_resolved", source="db_fallback", ws=db_name)
            return user_id, db_name, workspace_service.workspace_path(user_id, db_name)

    logger.info("active_workspace_miss", authenticated=user_id is not None)
    raise RuntimeError("Wywołaj activate_workspace() najpierw.")


def register_workspaces(
    mcp: FastMCP,
    workspace_service: WorkspaceService,
    oauth_repo: OAuthRepository,
    active_workspace_repo: ActiveWorkspaceRepository,
) -> None:
    _deps.oauth_repo = oauth_repo
    _deps.active_workspace_repo = active_workspace_repo

    @mcp.tool()
    @logged_tool
    async def list_workspaces(ctx: Context) -> str:
        """Zwraca listę workspace'ów dostępnych dla tego użytkownika.
        Odpowiedź: JSON array stringów."""
        user_id, err = await run_sync(_resolve_user)
        if err:
            return err
        return json.dumps(await run_sync(workspace_service.list_accessible, user_id))

    @mcp.tool()
    @logged_tool
    async def activate_workspace(name: str, ctx: Context) -> str:
        """Ustawia aktywny workspace dla tej sesji.
        Sukces: {"message": "..."}. Błąd: {"error": "...", "available": [...]}."""
        user_id, err = await run_sync(_resolve_user)
        if err:
            return err
        available = await run_sync(workspace_service.list_accessible, user_id)
        if name not in available:
            msg = (
                "Workspace '{name}' nie istnieje lub brak dostępu."
                if user_id
                else "Workspace '{name}' nie istnieje."
            )
            return json.dumps({"error": msg.format(name=name), "available": available})
        existing_owner_id = await ctx.get_state("active_owner_id")
        owner_id = user_id or existing_owner_id or f"anon-{generate(size=12)}"
        await ctx.set_state("active_workspace", name)
        await ctx.set_state("active_user_id", user_id)
        await ctx.set_state("active_owner_id", owner_id)
        # Persist per-user so it survives the connector's per-call session churn.
        # Authenticated users only — anon has no stable id to key on.
        if user_id is not None:
            await run_sync(active_workspace_repo.set, user_id, name)
        logger.info("workspace_switched", ws=name)
        return json.dumps({"message": f"Workspace '{name}' aktywny."})

    @mcp.tool()
    @logged_tool
    async def create_workspace(name: str, ctx: Context) -> str:
        """Tworzy nowy workspace z repozytorium git.
        Sukces: {"message": "..."}. Błąd: {"error": "..."}."""
        user_id, err = await run_sync(_resolve_user)
        if err:
            return err
        try:
            await run_sync(workspace_service.create, name, user_id)
        except (ValueError, FileExistsError) as e:
            return json.dumps({"error": str(e)})
        return json.dumps({"message": f"Workspace '{name}' utworzony."})
