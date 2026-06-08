import json
import os
from pathlib import Path

from fastmcp import Context, FastMCP

from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.workspace import (
    create_workspace as _create_workspace,
    list_workspaces as _list_workspaces,
)


async def get_active_workspace(ctx: Context) -> tuple[str, str]:
    name = await ctx.get_state("active_workspace")
    if not name:
        raise RuntimeError("Wywołaj activate_workspace() najpierw.")
    path = str(Path(os.getenv("WORKSPACES_DIR", "/workspaces")) / name)
    return name, path


def register_workspaces(
    mcp: FastMCP,
    workspace_repo: WorkspaceRepository,
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
            return json.dumps(workspace_repo.list_user_workspaces(user_id))
        return json.dumps(_list_workspaces())

    @mcp.tool()
    async def activate_workspace(name: str, ctx: Context) -> str:
        """Ustawia aktywny workspace dla tej sesji.
        Sukces: {"message": "..."}. Błąd: {"error": "...", "available": [...]}."""
        user_id, err = _resolve_user(ctx)
        if err:
            return err
        if user_id:
            available = workspace_repo.list_user_workspaces(user_id)
            if name not in available:
                return json.dumps({"error": f"Workspace '{name}' nie istnieje lub brak dostępu.", "available": available})
        else:
            if name not in _list_workspaces():
                return json.dumps({"error": f"Workspace '{name}' nie istnieje.", "available": _list_workspaces()})
        await ctx.set_state("active_workspace", name)
        return json.dumps({"message": f"Workspace '{name}' aktywny."})

    @mcp.tool()
    async def create_workspace(name: str, ctx: Context) -> str:
        """Tworzy nowy workspace z repozytorium git.
        Sukces: {"message": "..."}. Błąd: {"error": "..."}."""
        user_id, err = _resolve_user(ctx)
        if err:
            return err
        try:
            _create_workspace(name)
        except (ValueError, FileExistsError) as e:
            return json.dumps({"error": str(e)})
        if user_id:
            workspace_repo.grant_access(user_id, name)
        return json.dumps({"message": f"Workspace '{name}' utworzony."})
