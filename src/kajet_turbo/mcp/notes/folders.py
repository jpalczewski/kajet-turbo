from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.mcp.context import WORKSPACE_TARGET
from kajet_turbo.mcp.notes.types import (
    ConflictItem,
    FolderConflictResult,
    FolderContext,
    FolderInfo,
    MovedFolderResult,
    PrunedFoldersResult,
)
from kajet_turbo.mcp.tooling import publish_workspace_changed, read_tool, write_tool
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.targets import WorkspaceTarget
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.workspace import normalize_folder


def build_folders(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    folder_meta_repo: FolderMetaRepository,
) -> FastMCP:
    srv = FastMCP("notes-folders")

    @srv.tool(**read_tool(tags={"notes", "folders"}))
    async def list_folders(
        workspace: str,
        target: WorkspaceTarget = WORKSPACE_TARGET,
    ) -> list[FolderInfo]:
        """Returns the existing folders in the given workspace, with their descriptions.
        An empty path means the workspace root; description is empty when a folder has no
        metadata set.
        workspace: the workspace name to list folders in."""
        paths = await run_sync(note_service.list_folders, str(target.path))
        if not paths:
            return []
        meta_map = await run_sync(folder_meta_repo.get_many, target.owner_id, target.name, paths)
        return [
            FolderInfo(path=p, description=meta_map[p].description if p in meta_map else "")
            for p in paths
        ]

    @srv.tool(**write_tool(tags={"notes", "folders"}))
    async def set_folder_meta(
        folder: Annotated[
            str,
            Field(
                description="Folder path, e.g. 'Projekty/Klient A'. Empty string = workspace root."
            ),
        ],
        workspace: str,
        description: Annotated[
            str | None,
            Field(
                description="Short description of what this folder contains. Omit to keep existing."
            ),
        ] = None,
        instructions: Annotated[
            str | None,
            Field(
                description="LLM instructions shown when listing notes in this folder. "
                "Omit to keep existing."
            ),
        ] = None,
        target: WorkspaceTarget = WORKSPACE_TARGET,
    ) -> FolderContext:
        """Sets folder metadata, shown passively to the LLM in list_notes and list_folders.
        description: short description of what the folder contains.
        instructions: LLM instructions shown when listing notes in this folder.
        Omitting a parameter keeps its existing value.
        workspace: the workspace name the folder belongs to."""
        path = normalize_folder(folder)
        await run_sync(
            folder_meta_repo.set,
            target.owner_id,
            target.name,
            path,
            description=description,
            instructions=instructions,
        )
        meta = await run_sync(folder_meta_repo.get, target.owner_id, target.name, path)
        assert meta is not None
        return FolderContext.model_validate(meta)

    async def _move_folder(
        src: str, dst: str, target: WorkspaceTarget
    ) -> MovedFolderResult | FolderConflictResult:
        result = await run_sync(
            note_service.move_folder,
            src,
            dst,
            owner_id=target.owner_id,
            ws_path=str(target.path),
            workspace=target.name,
        )
        if "conflicts" in result:
            return FolderConflictResult(
                error=result["error"],
                conflicts=[ConflictItem.model_validate(c) for c in result["conflicts"]],
            )
        await publish_workspace_changed(target)
        return MovedFolderResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "folders"}))
    async def move_folder(
        src: str,
        dst: str,
        workspace: str,
        target: WorkspaceTarget = WORKSPACE_TARGET,
    ) -> MovedFolderResult | FolderConflictResult:
        """Moves/merges a folder (with its notes and subfolders) within the given workspace.
        If dst already exists, the folders are merged. On a note-title collision nothing is
        moved and the list of conflicts is returned.
        Success: {moved, src, dst}. Collision: {error, conflicts: [{title, folder}]}.
        workspace: the workspace name to operate in."""
        return await _move_folder(src, dst, target)

    @srv.tool(**write_tool(tags={"notes", "folders"}))
    async def rename_folder(
        folder: str,
        new_name: str,
        workspace: str,
        target: WorkspaceTarget = WORKSPACE_TARGET,
    ) -> MovedFolderResult | FolderConflictResult:
        """Renames a folder (within the same parent). new_name is the leaf name only,
        without a path — this also allows fixing letter case on a case-sensitive filesystem.
        Success: {moved, src, dst}. Collision: {error, conflicts: [{title, folder}]}.
        workspace: the workspace name to operate in."""
        parent = folder.rsplit("/", 1)[0] if "/" in folder.strip("/") else ""
        dst = f"{parent}/{new_name}" if parent else new_name
        return await _move_folder(folder, dst, target)

    @srv.tool(**write_tool(tags={"notes", "folders"}, idempotent=True))
    async def prune_empty_folders(
        workspace: str,
        target: WorkspaceTarget = WORKSPACE_TARGET,
    ) -> PrunedFoldersResult:
        """Removes empty directories (orphaned after moving notes). Folders containing
        .gitkeep are kept.
        workspace: the workspace name to prune."""
        result = await run_sync(note_service.prune_empty_folders, str(target.path))
        await publish_workspace_changed(target)
        return PrunedFoldersResult.model_validate(result)

    return srv
