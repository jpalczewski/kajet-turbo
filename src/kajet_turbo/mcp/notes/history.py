from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.context import ACTIVE_WORKSPACE, ActiveWorkspace
from kajet_turbo.mcp.notes.types import (
    HistoryEntry,
    NoteLinkItem,
    NoteLinksResult,
    SavedNoteResult,
)
from kajet_turbo.mcp.tooling import read_tool, write_tool
from kajet_turbo.services.notes import NoteData, NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def build_history(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    state_store=None,
) -> FastMCP:
    srv = FastMCP("notes-history", session_state_store=state_store)

    @srv.tool(**read_tool(tags={"notes", "history"}))
    @logged_tool
    async def get_note_history(
        note_id: str,
        limit: int = 50,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> list[HistoryEntry]:
        """Zwraca historię wersji notatki.
        Każdy wpis: {sha, message, timestamp}."""
        try:
            entries = await run_sync(
                note_service.get_history,
                note_id,
                owner_id=ws.owner_id,
                ws_path=ws.path,
                limit=limit,
            )
        except ValueError as e:
            raise ToolError(str(e)) from e
        return [HistoryEntry.model_validate(e) for e in entries]

    @srv.tool(**read_tool(tags={"notes", "history"}))
    @logged_tool
    async def get_note_at_version(
        note_id: str,
        sha: str,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> NoteData:
        """Zwraca treść notatki z konkretnego commita git.
        sha: pełny lub skrócony hash commita z get_note_history."""
        try:
            version = await run_sync(
                note_service.get_version, note_id, sha, owner_id=ws.owner_id, ws_path=ws.path
            )
        except Exception as e:
            raise ToolError(str(e)) from e
        return NoteData.model_validate(version)

    @srv.tool(**write_tool(tags={"notes", "history"}, destructive=True))
    @logged_tool
    async def restore_note_version(
        note_id: str,
        sha: str,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> SavedNoteResult:
        """Przywraca notatkę do wersji z podanego commita.
        sha: pełny lub skrócony hash z get_note_history."""
        try:
            result = await run_sync(
                note_service.restore_version, note_id, sha, owner_id=ws.owner_id, ws_path=ws.path
            )
        except Exception as e:
            raise ToolError(str(e)) from e
        return SavedNoteResult.model_validate(result)

    @srv.tool(**read_tool(tags={"notes", "links"}))
    @logged_tool
    async def get_note_links(
        note_id: str,
        include_meta: bool = False,
        include_cross_workspace: bool = True,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> NoteLinksResult:
        """Zwraca linki wychodzące i przychodzące dla notatki w aktywnym workspace.
        outlinks: notatki, do których ta notatka linkuje.
        backlinks: notatki, które linkują do tej notatki.
        include_meta=True dorzuca tags i updated_at do każdego wpisu.
        include_cross_workspace=False ogranicza backlinks tylko do tego samego workspace.
        Gdy wpis ma workspace != aktywny — to link cross-workspace; by go zapisać w treści
        użyj [[note:NOTE_ID]] (np. [[note:abc-123]]) zamiast [[Title]]."""
        result = await run_sync(
            note_service.links, note_id, ws.owner_id, include_meta, include_cross_workspace
        )
        if result is None:
            raise ToolError(f"Notatka {note_id} nie znaleziona.")
        return NoteLinksResult(
            outlinks=[NoteLinkItem.model_validate(link) for link in result["outlinks"]],
            backlinks=[NoteLinkItem.model_validate(link) for link in result["backlinks"]],
        )

    return srv
