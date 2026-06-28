from typing import Annotated

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.context import ACTIVE_WORKSPACE, MCP_CONTEXT, ActiveWorkspace
from kajet_turbo.mcp.notes._helpers import confirm_and_apply
from kajet_turbo.mcp.notes.types import TagItem, TagOperationResult
from kajet_turbo.mcp.tooling import read_tool, write_tool
from kajet_turbo.repositories.git import GitError
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def build_tags(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    state_store=None,
) -> FastMCP:
    srv = FastMCP("notes-tags", session_state_store=state_store)

    @srv.tool(**write_tool(tags={"notes", "tags"}, idempotent=True))
    @logged_tool
    async def add_tag(
        note_id: str,
        tags: list[str],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> TagOperationResult:
        """Dodaje tagi do frontmattera notatki (idempotentnie), bez ruszania treści.
        Uwaga: rusza tylko tagi z frontmattera; inline #hashtagi siedzą w treści."""
        try:
            result = await run_sync(note_service.add_tags, note_id, ws.owner_id, ws.path, tags)
        except (GitError, ValueError, FileNotFoundError) as e:
            raise ToolError(str(e)) from e
        return TagOperationResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "tags"}, idempotent=True))
    @logged_tool
    async def remove_tag(
        note_id: str,
        tags: list[str],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> TagOperationResult:
        """Usuwa tagi z frontmattera notatki (idempotentnie), bez ruszania treści.
        Tag obecny tylko jako inline #hashtag nie zniknie — wróci jako warning."""
        try:
            result = await run_sync(note_service.remove_tags, note_id, ws.owner_id, ws.path, tags)
        except (GitError, ValueError, FileNotFoundError) as e:
            raise ToolError(str(e)) from e
        return TagOperationResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "tags"}, destructive=True))
    @logged_tool
    async def set_tags(
        note_id: str,
        tags: list[str],
        confirm: bool = False,
        ctx: Context = MCP_CONTEXT,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> str:
        """Nadpisuje frontmatter tagów notatki podaną listą, bez ruszania treści.
        Destrukcyjne: jeśli usunęłoby istniejące tagi, prosi o potwierdzenie (elicitation;
        gdy klient nie wspiera — zwraca requires_confirmation, zawołaj ponownie z confirm=true).
        Zwraca {note_id, tags, frontmatter_tags, warnings}."""
        try:
            result = await run_sync(
                note_service.set_tags, note_id, ws.owner_id, ws.path, tags, confirm
            )
        except (GitError, ValueError, FileNotFoundError) as e:
            raise ToolError(str(e)) from e

        async def reapply() -> dict:
            return await run_sync(note_service.set_tags, note_id, ws.owner_id, ws.path, tags, True)

        return await confirm_and_apply(ctx, result, reapply)

    @srv.tool(**read_tool(tags={"notes", "tags"}))
    @logged_tool
    async def list_tags(
        folder: Annotated[
            str | None,
            Field(
                description="Opcjonalny filtr — licz tylko tagi notatek z tego folderu "
                "(np. 'Projekty/Klient A'). Brak = cały workspace."
            ),
        ] = None,
        include_subfolders: Annotated[
            bool,
            Field(description="Przy podanym folderze: czy wliczać podfoldery (domyślnie tak)."),
        ] = True,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> list[TagItem]:
        """Zwraca tagi aktywnego workspace z licznikami popularności,
        posortowane malejąco po liczbie notatek. Każdy element: {path, name, count}.
        Użyj do rekonesansu istniejących tagów przed tagowaniem — opcjonalnie
        zawężając do folderu."""
        tags_result = await run_sync(
            note_service.tag_counts,
            ws.name,
            owner_id=ws.owner_id,
            folder=folder,
            include_subfolders=include_subfolders,
        )
        return [TagItem.model_validate(t) for t in tags_result]

    return srv
