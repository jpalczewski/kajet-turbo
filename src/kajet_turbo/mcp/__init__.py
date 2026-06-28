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

_INSTRUCTIONS = """
Kajet — git-versioned markdown notebook.

## Workflow
1. list_workspaces → activate_workspace (required before any note operation)
2. list_folders / search_notes / list_notes to orient yourself
3. get_note / save_note / edit_note / save_notes for reads and writes

## Wikilink syntax (use in note content)
- [[Title]] — link to a note by title (workspace-wide search)
- [[Folder/SubFolder/Title]] — link by full folder path + title
- [[Target|Displayed text]] — link with display alias
- [[note:NOTE_ID]] — cross-workspace permanent link; NOTE_ID is the note_id UUID
  from any note response; renders as a clickable link to the note in its workspace

Use [[note:NOTE_ID]] when linking across workspaces — the title-based forms only
resolve within the active workspace.

## Identifiers
- note_id: stable UUID — use for get_note, edit_note, delete_note, get_note_links
- (folder, title): natural key — unique per workspace; folder is "" for workspace root
- Folder paths: slash-separated, e.g. "Projects/Client A"; "" = workspace root

## Folders
- list_folders returns folders with optional description
- list_notes with folder= filter returns folder_context.instructions if set — follow them
- set_folder_meta sets per-folder description and LLM instructions
"""


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
    mcp = FastMCP(
        "kajet-turbo",
        instructions=_INSTRUCTIONS,
        auth=provider,
        session_state_store=state_store,
    )
    mcp.mount(
        build_workspaces(
            workspace_service, oauth_repo, active_workspace_repo, state_store=state_store
        )
    )
    mcp.mount(build_notes(note_service, workspace_service, folder_meta_repo, state_store=state_store))
    return mcp
