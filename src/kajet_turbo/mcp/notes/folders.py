from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from kajet_turbo.api.schemas.ws import WorkspaceChangedEvent
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import event_repo
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.context import ACTIVE_WORKSPACE, ActiveWorkspace
from kajet_turbo.mcp.notes._types import (
    ConflictItem,
    FolderConflictResult,
    MovedFolderResult,
    PrunedFoldersResult,
)
from kajet_turbo.mcp.tooling import read_tool, write_tool
from kajet_turbo.repositories.git import GitError
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def build_folders(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    state_store=None,
) -> FastMCP:
    srv = FastMCP("notes-folders", session_state_store=state_store)

    @srv.tool(**read_tool(tags={"notes", "folders"}))
    @logged_tool
    async def list_folders(
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> list[str]:
        """Zwraca istniejące foldery aktywnego workspace.
        Pusty string oznacza katalog główny workspace."""
        return await run_sync(note_service.list_folders, ws.path)

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
