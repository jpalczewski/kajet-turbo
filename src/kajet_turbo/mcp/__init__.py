from fastmcp import FastMCP
from key_value.aio.stores.memory import MemoryStore

from kajet_turbo.auth import KajetOAuthProvider
from kajet_turbo.mcp.context import configure_mcp_context
from kajet_turbo.mcp.notes import build_notes
from kajet_turbo.mcp.workspaces import build_workspaces
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def build_mcp(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    folder_meta_repo: FolderMetaRepository,
    oauth_repo: OAuthRepository,
    active_workspace_repo: ActiveWorkspaceRepository,
    provider: KajetOAuthProvider,
) -> FastMCP:
    state_store = MemoryStore()
    configure_mcp_context(workspace_service, oauth_repo, active_workspace_repo)
    mcp = FastMCP("kajet-turbo", auth=provider, session_state_store=state_store)
    mcp.mount(
        build_workspaces(
            workspace_service, oauth_repo, active_workspace_repo, state_store=state_store
        )
    )
    mcp.mount(build_notes(note_service, workspace_service, folder_meta_repo, state_store=state_store))
    return mcp
