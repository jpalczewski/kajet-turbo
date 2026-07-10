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

## Read ergonomics
- grep_notes — literal substring search with line numbers; use instead of search_notes when
  you need exact-text certainty (search_notes ranks semantically/FTS, no literal guarantee)
- get_notes — read many note_ids in one call instead of N x get_note
- get_note_outline → edit_note(target_heading=...) — inspect a note's headings without
  loading its body, then edit just that section surgically
- export_folder — concatenate a folder's subtree into one markdown corpus, for
  reconnaissance across many related notes instead of N x get_note

## Batch editing
- edit_note(replace_all=true) — replace_text/delete_text on every match in one note
  instead of requiring old_text to be unique; response carries replaced with the count
- edit_notes — edit multiple existing notes in one atomic commit, all-or-nothing: any
  invalid item rejects the whole batch before anything is written. Content + tags only,
  no renames (use edit_note for that); unlike save_notes, never leaves a partial batch
- delete_notes — delete multiple notes in one atomic commit, all-or-nothing. Gated by
  expected_sha per item (the note's HEAD sha from get_note_history) instead of a plain
  confirm flag — a stale sha rejects the whole batch and reports current_sha to retry with
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
    mcp.mount(
        build_notes(note_service, workspace_service, folder_meta_repo, state_store=state_store)
    )
    return mcp
