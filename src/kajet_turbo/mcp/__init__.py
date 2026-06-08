from fastmcp import FastMCP

from kajet_turbo.auth import KajetOAuthProvider
from kajet_turbo.mcp.notes import register_notes
from kajet_turbo.mcp.workspaces import register_workspaces
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository


def build_mcp(
    note_repo: NoteRepository,
    workspace_repo: WorkspaceRepository,
    oauth_repo: OAuthRepository,
    provider: KajetOAuthProvider,
) -> FastMCP:
    mcp = FastMCP("kajet-turbo", auth=provider)
    register_workspaces(mcp, workspace_repo, oauth_repo)
    register_notes(mcp, note_repo)
    return mcp
