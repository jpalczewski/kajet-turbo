from fastmcp import FastMCP

from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.services.collections import CollectionService
from kajet_turbo.services.notes import (
    NoteCreateService,
    NoteDeleteService,
    NoteEditService,
    NoteFolderService,
    NoteLinkService,
    NoteReadService,
    NoteReconcileService,
    NoteSearchService,
    NoteTagService,
    NoteTemporalService,
    NoteVersionService,
)
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
    note_create_service: NoteCreateService,
    note_edit_service: NoteEditService,
    note_delete_service: NoteDeleteService,
    note_tag_service: NoteTagService,
    note_link_service: NoteLinkService,
    note_folder_service: NoteFolderService,
    note_temporal_service: NoteTemporalService,
    note_version_service: NoteVersionService,
    note_read_service: NoteReadService,
    note_reconcile_service: NoteReconcileService,
    note_search_service: NoteSearchService,
    workspace_service: WorkspaceService,
    folder_meta_repo: FolderMetaRepository,
    collection_service: CollectionService,
) -> FastMCP:
    srv = FastMCP("notes")
    srv.mount(build_write(note_create_service, note_edit_service, note_delete_service))
    srv.mount(build_read(note_read_service, folder_meta_repo))
    srv.mount(build_search(note_search_service, workspace_service))
    srv.mount(build_temporal(note_temporal_service, collection_service))
    srv.mount(build_maintenance(note_reconcile_service))
    srv.mount(build_folders(note_folder_service, workspace_service, folder_meta_repo))
    srv.mount(build_tags(note_tag_service, workspace_service))
    srv.mount(
        build_history(note_edit_service, note_version_service, note_link_service, workspace_service)
    )
    srv.mount(build_graph(note_link_service, workspace_service))
    return srv
