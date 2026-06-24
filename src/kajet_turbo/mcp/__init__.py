from fastmcp import FastMCP

from kajet_turbo.auth import KajetOAuthProvider
from kajet_turbo.mcp.notes import register_notes
from kajet_turbo.mcp.workspaces import register_workspaces
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def build_mcp(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    oauth_repo: OAuthRepository,
    active_workspace_repo: ActiveWorkspaceRepository,
    provider: KajetOAuthProvider,
) -> FastMCP:
    mcp = FastMCP("kajet-turbo", auth=provider)
    register_workspaces(mcp, workspace_service, oauth_repo, active_workspace_repo)
    register_notes(mcp, note_service, workspace_service)
    return mcp
