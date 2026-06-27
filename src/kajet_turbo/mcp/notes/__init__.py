from fastmcp import FastMCP

from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService

from .crud import build_crud
from .folders import build_folders
from .history import build_history
from .tags import build_tags


def build_notes(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    state_store=None,
) -> FastMCP:
    srv = FastMCP("notes", session_state_store=state_store)
    srv.mount(build_crud(note_service, workspace_service, state_store=state_store))
    srv.mount(build_folders(note_service, workspace_service, state_store=state_store))
    srv.mount(build_tags(note_service, workspace_service, state_store=state_store))
    srv.mount(build_history(note_service, workspace_service, state_store=state_store))
    return srv
