from fastmcp import FastMCP

from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.services.collections import CollectionService
from kajet_turbo.services.notes import NoteReadService, NoteService, NoteTemporalService
from kajet_turbo.services.workspaces import WorkspaceService

from .folders import build_folders
from .graph import build_graph
from .history import build_history
from .maintenance import build_maintenance
from .read import build_read
from .search import build_search
from .tags import build_tags
from .temporal import build_temporal
from .write import build_write


def build_notes(
    note_service: NoteService,
    note_temporal_service: NoteTemporalService,
    note_read_service: NoteReadService,
    workspace_service: WorkspaceService,
    folder_meta_repo: FolderMetaRepository,
    collection_service: CollectionService,
) -> FastMCP:
    srv = FastMCP("notes")
    srv.mount(build_write(note_service))
    srv.mount(build_read(note_read_service, folder_meta_repo))
    srv.mount(build_search(note_service, workspace_service))
    srv.mount(build_temporal(note_temporal_service, collection_service))
    srv.mount(build_maintenance(note_service))
    srv.mount(build_folders(note_service, workspace_service, folder_meta_repo))
    srv.mount(build_tags(note_service, workspace_service))
    srv.mount(build_history(note_service, workspace_service))
    srv.mount(build_graph(note_service, workspace_service))
    return srv
