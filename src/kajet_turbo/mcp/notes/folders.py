from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.notes._types import (
    ConflictItem,
    FolderConflictResult,
    MovedFolderResult,
    PrunedFoldersResult,
)
from kajet_turbo.mcp.workspaces import get_active_workspace
from kajet_turbo.repositories.git import GitError
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def build_folders(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    state_store=None,
) -> FastMCP:
    srv = FastMCP("notes-folders", session_state_store=state_store)

    @srv.tool()
    @logged_tool
    async def list_folders(ctx: Context) -> list[str]:
        """Zwraca istniejące foldery aktywnego workspace.
        Pusty string oznacza katalog główny workspace."""
        try:
            _, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e)) from None
        return await run_sync(note_service.list_folders, ws_path)

    @srv.tool()
    @logged_tool
    async def move_folder(
        src: str, dst: str, ctx: Context
    ) -> MovedFolderResult | FolderConflictResult:
        """Przenosi/scala folder (z notatkami i podfolderami) w aktywnym workspace.
        Jeśli dst istnieje, foldery są scalane. Przy kolizji nazw notatek nic nie jest
        przenoszone i zwracana jest lista kolizji.
        Sukces: {moved, src, dst}. Kolizja: {error, conflicts: [{title, folder}]}."""
        try:
            owner_id, ws_name, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e)) from None
        try:
            result = await run_sync(
                note_service.move_folder,
                src,
                dst,
                owner_id=owner_id,
                ws_path=ws_path,
                workspace=ws_name,
            )
        except (ValueError, FileNotFoundError, FileExistsError, GitError) as e:
            raise ToolError(str(e)) from e
        if "conflicts" in result:
            return FolderConflictResult(
                error=result["error"],
                conflicts=[ConflictItem.model_validate(c) for c in result["conflicts"]],
            )
        return MovedFolderResult.model_validate(result)

    @srv.tool()
    @logged_tool
    async def rename_folder(
        folder: str, new_name: str, ctx: Context
    ) -> MovedFolderResult | FolderConflictResult:
        """Zmienia nazwę folderu (w obrębie tego samego rodzica). new_name to sama nazwa
        liścia, bez ścieżki. Pozwala m.in. zmienić wielkość liter na case-sensitive FS.
        Sukces: {moved, src, dst}. Kolizja: {error, conflicts: [{title, folder}]}."""
        parent = folder.rsplit("/", 1)[0] if "/" in folder.strip("/") else ""
        dst = f"{parent}/{new_name}" if parent else new_name
        try:
            owner_id, ws_name, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e)) from None
        try:
            result = await run_sync(
                note_service.move_folder,
                folder,
                dst,
                owner_id=owner_id,
                ws_path=ws_path,
                workspace=ws_name,
            )
        except (ValueError, FileNotFoundError, FileExistsError, GitError) as e:
            raise ToolError(str(e)) from e
        if "conflicts" in result:
            return FolderConflictResult(
                error=result["error"],
                conflicts=[ConflictItem.model_validate(c) for c in result["conflicts"]],
            )
        return MovedFolderResult.model_validate(result)

    @srv.tool()
    @logged_tool
    async def prune_empty_folders(ctx: Context) -> PrunedFoldersResult:
        """Usuwa puste katalogi (osierocone po przenoszeniu notatek).
        Foldery z .gitkeep są zachowane."""
        try:
            _, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e)) from None
        result = await run_sync(note_service.prune_empty_folders, ws_path)
        return PrunedFoldersResult.model_validate(result)

    return srv
