from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.notes._types import (
    HistoryEntry,
    NoteLinkItem,
    NoteLinksResult,
    SavedNoteResult,
)
from kajet_turbo.mcp.workspaces import get_active_workspace
from kajet_turbo.services.notes import NoteData, NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def build_history(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    state_store=None,
) -> FastMCP:
    srv = FastMCP("notes-history", session_state_store=state_store)

    @srv.tool()
    @logged_tool
    async def get_note_history(note_id: str, ctx: Context, limit: int = 50) -> list[HistoryEntry]:
        """Zwraca historię wersji notatki.
        Każdy wpis: {sha, message, timestamp}."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e)) from None
        try:
            entries = await run_sync(
                note_service.get_history, note_id, owner_id=owner_id, ws_path=ws_path, limit=limit
            )
        except ValueError as e:
            raise ToolError(str(e)) from e
        return [HistoryEntry.model_validate(e) for e in entries]

    @srv.tool()
    @logged_tool
    async def get_note_at_version(note_id: str, sha: str, ctx: Context) -> NoteData:
        """Zwraca treść notatki z konkretnego commita git.
        sha: pełny lub skrócony hash commita z get_note_history."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e)) from None
        try:
            version = await run_sync(
                note_service.get_version, note_id, sha, owner_id=owner_id, ws_path=ws_path
            )
        except Exception as e:
            raise ToolError(str(e)) from e
        return NoteData.model_validate(version)

    @srv.tool()
    @logged_tool
    async def restore_note_version(note_id: str, sha: str, ctx: Context) -> SavedNoteResult:
        """Przywraca notatkę do wersji z podanego commita.
        sha: pełny lub skrócony hash z get_note_history."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e)) from None
        try:
            result = await run_sync(
                note_service.restore_version, note_id, sha, owner_id=owner_id, ws_path=ws_path
            )
        except Exception as e:
            raise ToolError(str(e)) from e
        return SavedNoteResult.model_validate(result)

    @srv.tool()
    @logged_tool
    async def get_note_links(
        note_id: str,
        ctx: Context,
        include_meta: bool = False,
    ) -> NoteLinksResult:
        """Zwraca linki wychodzące i przychodzące dla notatki w aktywnym workspace.
        outlinks: notatki, do których ta notatka linkuje.
        backlinks: notatki, które linkują do tej notatki.
        include_meta=True dorzuca tags i updated_at do każdego wpisu."""
        try:
            owner_id, _, _ = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e)) from None
        result = await run_sync(note_service.links, note_id, owner_id, include_meta)
        if result is None:
            raise ToolError(f"Notatka {note_id} nie znaleziona.")
        return NoteLinksResult(
            outlinks=[NoteLinkItem.model_validate(link) for link in result["outlinks"]],
            backlinks=[NoteLinkItem.model_validate(link) for link in result["backlinks"]],
        )

    return srv
