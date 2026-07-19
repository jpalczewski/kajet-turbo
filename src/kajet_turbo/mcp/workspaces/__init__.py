from fastmcp import FastMCP

from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.services.workspaces import WorkspaceService

from .meta import build_meta
from .settings import build_settings


def build_workspaces(
    workspace_service: WorkspaceService,
    active_workspace_repo: ActiveWorkspaceRepository,
    state_store=None,
) -> FastMCP:
    srv = FastMCP("workspaces", session_state_store=state_store)
    srv.mount(build_meta(workspace_service, active_workspace_repo, state_store=state_store))
    srv.mount(build_settings(workspace_service, state_store=state_store))
    return srv


__all__ = ["build_workspaces"]
