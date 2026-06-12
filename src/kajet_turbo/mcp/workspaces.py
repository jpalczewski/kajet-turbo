import json

from fastmcp import Context, FastMCP
from fastmcp.server.dependencies import get_access_token
from nanoid import generate

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool, logger
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.services.workspaces import WorkspaceService


async def get_active_workspace(
    ctx: Context, workspace_service: WorkspaceService
) -> tuple[str, str, str]:
    """Returns (owner_id, workspace_slug, workspace_path)."""
    name = await ctx.get_state("active_workspace")
    if not name:
        raise RuntimeError("Wywołaj activate_workspace() najpierw.")
    owner_id: str = await ctx.get_state("active_owner_id")
    real_user_id: str | None = await ctx.get_state("active_user_id")
    path = workspace_service.workspace_path(real_user_id, name)
    return owner_id, name, path


def register_workspaces(
    mcp: FastMCP,
    workspace_service: WorkspaceService,
    oauth_repo: OAuthRepository,
) -> None:
    def _resolve_user() -> tuple[str | None, str | None]:
        token = get_access_token()
        if token is None:
            return None, None
        user_id = oauth_repo.get_user_id_by_client(token.client_id)
        if user_id is None:
            return None, json.dumps({"error": "unauthorized"})
        return user_id, None

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
