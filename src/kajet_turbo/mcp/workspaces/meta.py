from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from nanoid import generate

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool, logger
from kajet_turbo.mcp.context import (
    active_workspace_scope,
    require_user_id,
    require_workspace_access,
    resolve_user_id,
)
from kajet_turbo.mcp.tooling import read_tool, write_tool
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.services.workspaces import WorkspaceService

from .types import WorkspaceInfo, WorkspaceMessageResult, WorkspacesResult, WorkspaceUpdatedResult


def build_meta(
    workspace_service: WorkspaceService,
    active_workspace_repo: ActiveWorkspaceRepository,
    state_store=None,
) -> FastMCP:
    srv = FastMCP("workspaces-meta", session_state_store=state_store)

    @srv.tool(**read_tool(tags={"workspace", "metadata"}))
    @logged_tool
    async def list_workspaces(ctx: Context) -> WorkspacesResult:
        """Zwraca workspace'y dostępne dla użytkownika wraz z metadanymi.
        Użyj `description`, by wybrać właściwy workspace przed activate_workspace()."""
        del ctx
        user_id = await resolve_user_id()
        workspaces = await run_sync(workspace_service.list_meta, user_id)
        return WorkspacesResult(workspaces=[WorkspaceInfo.model_validate(w) for w in workspaces])

    @srv.tool(**write_tool(tags={"workspace", "state"}, idempotent=True))
    @logged_tool
    async def activate_workspace(name: str, ctx: Context) -> WorkspaceMessageResult:
        """Ustawia aktywny workspace dla tej sesji."""
        user_id = await resolve_user_id()
        await require_workspace_access(name, user_id)
        existing_owner_id = await ctx.get_state("active_owner_id")
        owner_id = user_id or existing_owner_id or f"anon-{generate(size=12)}"
        await ctx.set_state("active_workspace", name)
        await ctx.set_state("active_user_id", user_id)
        await ctx.set_state("active_owner_id", owner_id)
        if user_id is not None:
            scope = active_workspace_scope(ctx)
            if scope is not None:
                await run_sync(active_workspace_repo.set, user_id, name, scope)
        else:
            scope = None
        logger.info("workspace_switched", ws=name, scope=scope)
        return WorkspaceMessageResult(message=f"Workspace '{name}' aktywny.", workspace=name)

    @srv.tool(**write_tool(tags={"workspace", "metadata"}, idempotent=False))
    @logged_tool
    async def create_workspace(
        name: str, ctx: Context, description: str = ""
    ) -> WorkspaceMessageResult:
        """Tworzy nowy workspace z repozytorium git.
        `description` (opcjonalnie) opisuje do czego workspace służy."""
        del ctx
        user_id = await resolve_user_id()
        try:
            await run_sync(workspace_service.create, name, user_id, description=description)
        except (ValueError, FileExistsError) as e:
            raise ToolError(str(e)) from e
        return WorkspaceMessageResult(message=f"Workspace '{name}' utworzony.", workspace=name)

    @srv.tool(**write_tool(tags={"workspace", "metadata"}, idempotent=True))
    @logged_tool
    async def update_workspace(
        name: str,
        ctx: Context,
        description: str | None = None,
        tags: list[str] | None = None,
    ) -> WorkspaceUpdatedResult:
        """Ustawia metadane workspace'u: opis (do czego służy) i/lub tagi.
        Foldery ustawiasz z UI, nie tym narzędziem."""
        del ctx
        user_id = await require_user_id()
        await require_workspace_access(name, user_id)
        try:
            result = await run_sync(
                workspace_service.set_meta, user_id, name, description=description, tags=tags
            )
        except ValueError as e:
            raise ToolError(str(e)) from e
        return WorkspaceUpdatedResult(
            message=f"Workspace '{name}' zaktualizowany.",
            workspace=name,
            description=result["description"],
            tags=result["tags"],
        )

    return srv
