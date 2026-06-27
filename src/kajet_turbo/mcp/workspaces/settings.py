from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

from kajet_turbo import workspace_settings
from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.context import require_user_id, require_workspace_access
from kajet_turbo.mcp.tooling import read_tool, write_tool
from kajet_turbo.services.workspaces import WorkspaceService

from .types import (
    SettingKey,
    WorkspaceSettingInfo,
    WorkspaceSettingsResult,
    WorkspaceSettingUpdatedResult,
)


def build_settings(workspace_service: WorkspaceService, state_store=None) -> FastMCP:
    srv = FastMCP("workspaces-settings", session_state_store=state_store)

    @srv.tool(**read_tool(tags={"workspace", "settings"}))
    @logged_tool
    async def list_workspace_settings(name: str, ctx: Context) -> WorkspaceSettingsResult:
        """Zwraca ustawienia workspace'u i ich definicje."""
        del ctx
        user_id = await require_user_id()
        await require_workspace_access(name, user_id)
        values = await run_sync(workspace_service.get_settings, user_id, name)
        settings = [{**d, "value": values[d["key"]]} for d in workspace_settings.definitions()]
        return WorkspaceSettingsResult(
            settings=[WorkspaceSettingInfo.model_validate(s) for s in settings]
        )

    @srv.tool(**write_tool(tags={"workspace", "settings"}, idempotent=True))
    @logged_tool
    async def set_workspace_setting(
        name: str,
        setting: SettingKey,
        value: bool | int | str,
        ctx: Context,
    ) -> WorkspaceSettingUpdatedResult:
        """Ustawia pojedyncze ustawienie workspace'u.
        `setting` to klucz z list_workspace_settings; `value` zgodny z typem ustawienia."""
        del ctx
        user_id = await require_user_id()
        await require_workspace_access(name, user_id)
        key = setting.value
        try:
            result = await run_sync(workspace_service.set_setting, user_id, name, key, value)
        except ValueError as e:
            raise ToolError(str(e)) from e
        return WorkspaceSettingUpdatedResult(
            message=f"Ustawienie '{key}' zaktualizowane.",
            workspace=name,
            setting=key,
            value=result[key],
        )

    return srv
