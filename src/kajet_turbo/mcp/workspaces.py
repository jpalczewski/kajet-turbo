import json
import os
from pathlib import Path

from fastmcp import Context, FastMCP

from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.workspace import list_workspaces as _list_workspaces


async def get_active_workspace(ctx: Context) -> tuple[str | None, str, str]:
    """Returns (user_id, workspace_slug, workspace_path)."""
    name = await ctx.get_state("active_workspace")
    if not name:
        raise RuntimeError("Wywołaj activate_workspace() najpierw.")
    user_id: str | None = await ctx.get_state("active_user_id")
    base = Path(os.getenv("WORKSPACES_DIR", "/workspaces"))
    path = str(base / user_id / name) if user_id else str(base / name)
    return user_id, name, path


def register_workspaces(
    mcp: FastMCP,
    workspace_service: WorkspaceService,
    oauth_repo: OAuthRepository,
) -> None:
    def _resolve_user(ctx: Context) -> tuple[str | None, str | None]:
        client_id = ctx.client_id
        if client_id is None:
            return None, None
        user_id = oauth_repo.get_user_id_by_client(client_id)
        if user_id is None:
            return None, json.dumps({"error": "unauthorized"})
        return user_id, None

    @mcp.tool()
    def ping() -> str:
        """Health check."""
        return "pong"

    @mcp.tool()
    async def list_workspaces(ctx: Context) -> str:
        """Zwraca listę workspace'ów dostępnych dla tego użytkownika. Odpowiedź: JSON array stringów."""
        user_id, err = _resolve_user(ctx)
        if err:
            return err
        if user_id:
            return json.dumps(workspace_service.list_for_user(user_id))
        return json.dumps(_list_workspaces())

    @mcp.tool()
    async def activate_workspace(name: str, ctx: Context) -> str:
        """Ustawia aktywny workspace dla tej sesji.
        Sukces: {"message": "..."}. Błąd: {"error": "...", "available": [...]}."""
        user_id, err = _resolve_user(ctx)
        if err:
            return err
        if user_id:
            if not workspace_service.has_access(user_id, name):
                available = workspace_service.list_for_user(user_id)
                return json.dumps({"error": f"Workspace '{name}' nie istnieje lub brak dostępu.", "available": available})
        else:
            if name not in _list_workspaces():
                return json.dumps({"error": f"Workspace '{name}' nie istnieje.", "available": _list_workspaces()})
        await ctx.set_state("active_workspace", name)
        await ctx.set_state("active_user_id", user_id)
        return json.dumps({"message": f"Workspace '{name}' aktywny."})

    @mcp.tool()
    async def create_workspace(name: str, ctx: Context) -> str:
        """Tworzy nowy workspace z repozytorium git.
        Sukces: {"message": "..."}. Błąd: {"error": "..."}."""
        user_id, err = _resolve_user(ctx)
        if err:
            return err
        try:
            workspace_service.create(name, user_id)
        except (ValueError, FileExistsError) as e:
            return json.dumps({"error": str(e)})
        return json.dumps({"message": f"Workspace '{name}' utworzony."})
