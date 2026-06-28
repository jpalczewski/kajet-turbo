from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from kajet_turbo.api.schemas.ws import WorkspaceChangedEvent
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import event_repo
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.context import ACTIVE_WORKSPACE, ActiveWorkspace
from kajet_turbo.mcp.notes.types import (
    ConflictItem,
    FolderConflictResult,
    FolderContext,
    FolderInfo,
    MovedFolderResult,
    PrunedFoldersResult,
)
from kajet_turbo.mcp.tooling import read_tool, write_tool
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.repositories.git import GitError
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.workspace import normalize_folder


def build_folders(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    folder_meta_repo: FolderMetaRepository,
    state_store=None,
) -> FastMCP:
    srv = FastMCP("notes-folders", session_state_store=state_store)

    @srv.tool(**read_tool(tags={"notes", "folders"}))
    @logged_tool
    async def list_folders(
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> list[FolderInfo]:
        """Zwraca istniejące foldery aktywnego workspace z ich opisami.
        Pusty string w path oznacza katalog główny workspace.
        description jest pusty gdy folder nie ma ustawionych metadanych."""
        paths = await run_sync(note_service.list_folders, ws.path)
        if not paths:
            return []
        meta_map = await run_sync(folder_meta_repo.get_many, ws.owner_id, ws.name, paths)
        return [
            FolderInfo(path=p, description=meta_map[p].description if p in meta_map else "")
            for p in paths
        ]

    @srv.tool(**write_tool(tags={"notes", "folders"}))
    @logged_tool
    async def set_folder_meta(
        folder: Annotated[
            str,
            Field(description="Folder path, e.g. 'Projekty/Klient A'. Empty string = workspace root."),
        ],
        description: Annotated[
            str | None,
            Field(description="Short description of what this folder contains. Omit to keep existing."),
        ] = None,
        instructions: Annotated[
            str | None,
            Field(description="LLM instructions shown when listing notes in this folder. Omit to keep existing."),
        ] = None,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> FolderContext:
        """Ustawia metadane folderu widoczne pasywnie dla LLM-a w list_notes i list_folders.
        description: krótki opis co zawiera folder.
        instructions: instrukcje dla LLM-a wyświetlane przy list_notes dla tego folderu.
        Pominięcie parametru zachowuje istniejącą wartość."""
        path = normalize_folder(folder)
        await run_sync(
            folder_meta_repo.set,
            ws.owner_id,
            ws.name,
            path,
            description=description,
            instructions=instructions,
        )
        meta = await run_sync(folder_meta_repo.get, ws.owner_id, ws.name, path)
        assert meta is not None
        return FolderContext.model_validate(meta)

    @srv.tool(**write_tool(tags={"notes", "folders"}))
    @logged_tool
    async def move_folder(
        src: str,
        dst: str,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> MovedFolderResult | FolderConflictResult:
        """Przenosi/scala folder (z notatkami i podfolderami) w aktywnym workspace.
        Jeśli dst istnieje, foldery są scalane. Przy kolizji nazw notatek nic nie jest
        przenoszone i zwracana jest lista kolizji.
        Sukces: {moved, src, dst}. Kolizja: {error, conflicts: [{title, folder}]}."""
        try:
            result = await run_sync(
                note_service.move_folder,
                src,
                dst,
                owner_id=ws.owner_id,
                ws_path=ws.path,
                workspace=ws.name,
            )
        except (ValueError, FileNotFoundError, FileExistsError, GitError) as e:
            raise ToolError(str(e)) from e
        if "conflicts" in result:
            return FolderConflictResult(
                error=result["error"],
                conflicts=[ConflictItem.model_validate(c) for c in result["conflicts"]],
            )
        await run_sync(
            event_repo.publish,
            ws.owner_id,
            "workspace_changed",
            WorkspaceChangedEvent(
                type="workspace_changed",
                owner_id=ws.owner_id,
                workspace=ws.name,
            ).model_dump(),
        )
        return MovedFolderResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "folders"}))
    @logged_tool
    async def rename_folder(
        folder: str,
        new_name: str,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> MovedFolderResult | FolderConflictResult:
        """Zmienia nazwę folderu (w obrębie tego samego rodzica). new_name to sama nazwa
        liścia, bez ścieżki. Pozwala m.in. zmienić wielkość liter na case-sensitive FS.
        Sukces: {moved, src, dst}. Kolizja: {error, conflicts: [{title, folder}]}."""
        parent = folder.rsplit("/", 1)[0] if "/" in folder.strip("/") else ""
        dst = f"{parent}/{new_name}" if parent else new_name
        try:
            result = await run_sync(
                note_service.move_folder,
                folder,
                dst,
                owner_id=ws.owner_id,
                ws_path=ws.path,
                workspace=ws.name,
            )
        except (ValueError, FileNotFoundError, FileExistsError, GitError) as e:
            raise ToolError(str(e)) from e
        if "conflicts" in result:
            return FolderConflictResult(
                error=result["error"],
                conflicts=[ConflictItem.model_validate(c) for c in result["conflicts"]],
            )
        await run_sync(
            event_repo.publish,
            ws.owner_id,
            "workspace_changed",
            WorkspaceChangedEvent(
                type="workspace_changed",
                owner_id=ws.owner_id,
                workspace=ws.name,
            ).model_dump(),
        )
        return MovedFolderResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "folders"}, idempotent=True))
    @logged_tool
    async def prune_empty_folders(
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> PrunedFoldersResult:
        """Usuwa puste katalogi (osierocone po przenoszeniu notatek).
        Foldery z .gitkeep są zachowane."""
        result = await run_sync(note_service.prune_empty_folders, ws.path)
        await run_sync(
            event_repo.publish,
            ws.owner_id,
            "workspace_changed",
            WorkspaceChangedEvent(
                type="workspace_changed",
                owner_id=ws.owner_id,
                workspace=ws.name,
            ).model_dump(),
        )
        return PrunedFoldersResult.model_validate(result)

    return srv
