from typing import Annotated

from fastmcp import FastMCP
from fastmcp.server.context import Context
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.context import (
    ACTIVE_WORKSPACE,
    MCP_CONTEXT,
    ActiveWorkspace,
    active_workspace,
    require_user_id,
    require_workspace_access,
)
from kajet_turbo.mcp.notes.types import GrepMatch, GrepResult, SearchChunkResult
from kajet_turbo.mcp.tooling import read_tool
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def build_search(
    note_service: NoteService, workspace_service: WorkspaceService, state_store=None
) -> FastMCP:
    srv = FastMCP("notes-search", session_state_store=state_store)

    @srv.tool(**read_tool(tags={"notes", "search"}))
    @logged_tool
    async def search_notes(
        query: str,
        workspace: str = "active",
        limit: int = 10,
        folder: Annotated[
            str | None,
            Field(
                description="Restrict search to notes in this folder and its descendants, "
                "for example 'Projects/Client A'."
            ),
        ] = None,
        tags: Annotated[
            list[str] | None,
            Field(
                description="Restrict search to notes with these tags (OR, hierarchical, "
                "as in list_notes)."
            ),
        ] = None,
        ctx: Context = MCP_CONTEXT,
    ) -> list[SearchChunkResult]:
        """Search notes using chunk-level hybrid ranking: FTS, semantic similarity, and
        exact title/tag/folder matches.
        workspace='active' (default) searches the active workspace and requires prior
        activation. workspace='all' needs no activation and searches every accessible
        workspace that allows global search. Passing an exact workspace name also needs no
        activation and searches that workspace even when it is excluded from 'all'.
        folder and tags narrow the candidate notes; when both are present they intersect.
        Returns chunks with note_id, title, folder, updated_at, header_path, content, score,
        and optional matched_on. It never returns a complete note. Use search_notes to find
        note IDs, then get_note or get_notes for complete current content. Cross-workspace
        note IDs can be linked with [[note:NOTE_ID]]. Returns [] when nothing matches."""
        ws_param = workspace or "active"
        if ws_param == "active":
            ws = await active_workspace(ctx)
            workspaces = [ws.name]
            owner_id = ws.owner_id
        else:
            owner_id = await require_user_id()
            if ws_param == "all":
                workspaces = await run_sync(workspace_service.list_searchable_in_all, owner_id)
            else:
                await require_workspace_access(ws_param, owner_id)
                workspaces = [ws_param]
        if not workspaces:
            return []
        # search_async borrows a run_sync slot only for the ms-scale DB phases; the
        # query-embedding HTTP call is awaited natively on the event loop.
        results = await note_service.search_async(
            query,
            workspaces,
            owner_id=owner_id,
            limit=limit,
            folder=folder,
            tags=tags,
        )
        return [SearchChunkResult.model_validate(r) for r in results]

    @srv.tool(**read_tool(tags={"notes", "search"}))
    @logged_tool
    async def grep_notes(
        pattern: str,
        folder: Annotated[
            str | None,
            Field(description="Zawęź do notatek w tym folderze i podfolderach."),
        ] = None,
        case_sensitive: bool = False,
        max_results: int = 100,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> GrepResult:
        """Literalny (nie semantyczny) grep po treści notatek, z numerami linii.
        Użyj zamiast search_notes, gdy potrzebujesz pewności dokładnego dopasowania
        stringa (refaktor nazwy, weryfikacja "czy fraza gdzieś jeszcze została") —
        search_notes szuka znaczeniowo i nie gwarantuje trafienia literalnego tekstu.
        Przeszukuje surowy plik notatki, łącznie z frontmatter (id/title/tags/daty)."""
        result = await run_sync(
            note_service.grep,
            ws.name,
            ws.path,
            pattern,
            folder=folder,
            case_sensitive=case_sensitive,
            max_results=max_results,
        )
        return GrepResult(
            matches=[GrepMatch.model_validate(m) for m in result["matches"]],
            truncated=result["truncated"],
        )

    return srv
