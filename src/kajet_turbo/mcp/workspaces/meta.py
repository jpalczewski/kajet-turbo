from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.context import require_user_id, require_workspace_access
from kajet_turbo.mcp.tooling import read_tool, write_tool
from kajet_turbo.services.workspaces import WorkspaceService

from .types import WorkspaceInfo, WorkspaceMessageResult, WorkspacesResult, WorkspaceUpdatedResult


def build_meta(workspace_service: WorkspaceService) -> FastMCP:
    srv = FastMCP("workspaces-meta")

    @srv.tool(**read_tool(tags={"workspace", "metadata"}))
    @logged_tool
    async def list_workspaces(ctx: Context) -> WorkspacesResult:
        """Returns the workspaces available to the user, with metadata.
        Use `description` to pick the right workspace to pass as the `workspace`
        parameter on workspace-scoped tools."""
        del ctx
        user_id = await require_user_id()
        workspaces = await run_sync(workspace_service.list_meta, user_id)
        return WorkspacesResult(workspaces=[WorkspaceInfo.model_validate(w) for w in workspaces])

    @srv.tool(**write_tool(tags={"workspace", "metadata"}, idempotent=False))
    @logged_tool
    async def create_workspace(
        name: str, ctx: Context, description: str = ""
    ) -> WorkspaceMessageResult:
        """Tworzy nowy workspace z repozytorium git.
        `description` (opcjonalnie) opisuje do czego workspace służy."""
        del ctx
        user_id = await require_user_id()
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
