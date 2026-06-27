from fastmcp import FastMCP

from kajet_turbo.mcp.context import configure_mcp_context, get_active_workspace
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.services.workspaces import WorkspaceService

from .meta import build_meta
from .settings import build_settings


def build_workspaces(
    workspace_service: WorkspaceService,
    oauth_repo: OAuthRepository,
    active_workspace_repo: ActiveWorkspaceRepository,
    state_store=None,
) -> FastMCP:
    configure_mcp_context(workspace_service, oauth_repo, active_workspace_repo)
    srv = FastMCP("workspaces", session_state_store=state_store)
    srv.mount(build_meta(workspace_service, active_workspace_repo, state_store=state_store))
    srv.mount(build_settings(workspace_service, state_store=state_store))
    return srv


def register_workspaces(
    mcp: FastMCP,
    workspace_service: WorkspaceService,
    oauth_repo: OAuthRepository,
    active_workspace_repo: ActiveWorkspaceRepository,
) -> None:
    mcp.mount(build_workspaces(workspace_service, oauth_repo, active_workspace_repo))


__all__ = ["build_workspaces", "get_active_workspace", "register_workspaces"]
