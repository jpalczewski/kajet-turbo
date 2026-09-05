from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.mcp.context import require_user_id, require_workspace_access
from kajet_turbo.mcp.notes.types import SearchChunkResult
from kajet_turbo.mcp.tooling import read_tool
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def build_search(note_service: NoteService, workspace_service: WorkspaceService) -> FastMCP:
    srv = FastMCP("notes-search")

    @srv.tool(**read_tool(tags={"notes", "search"}))
    async def search_notes(
        query: str,
        workspace: str = "all",
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
    ) -> list[SearchChunkResult]:
        """Search notes using chunk-level hybrid ranking: FTS, semantic similarity, and
        exact title/tag/folder matches.
        workspace='all' (default) searches every accessible workspace that allows global
        search. Passing an exact workspace name searches that workspace even when it is
        excluded from 'all'.
        folder and tags narrow the candidate notes; when both are present they intersect.
        Returns chunks with note_id, title, folder, updated_at, header_path, content, score,
        and optional matched_on. It never returns a complete note. Use search_notes to find
        note IDs, then get_note or get_notes for complete current content. Cross-workspace
        note IDs can be linked with [[note:NOTE_ID]]. Returns [] when nothing matches."""
        ws_param = workspace or "all"
        if ws_param == "active":
            raise ToolError(
                "workspace='active' was removed: there is no session-active workspace "
                "anymore. Pass an explicit workspace name, or omit workspace / pass 'all' "
                "to search every accessible workspace."
            )
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

    return srv
