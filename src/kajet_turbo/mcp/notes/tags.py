from typing import Annotated

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.errors import GitError as GitErrorCode
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.notes._helpers import confirm_and_apply
from kajet_turbo.mcp.notes._types import TagItem, TagOperationResult
from kajet_turbo.mcp.workspaces import get_active_workspace
from kajet_turbo.repositories.git import GitError
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def build_tags(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    state_store=None,
) -> FastMCP:
    srv = FastMCP("notes-tags", session_state_store=state_store)

    @srv.tool()
    @logged_tool
    async def add_tag(note_id: str, tags: list[str], ctx: Context) -> TagOperationResult:
        """Dodaje tagi do frontmattera notatki (idempotentnie), bez ruszania treści.
        Uwaga: rusza tylko tagi z frontmattera; inline #hashtagi siedzą w treści."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e))
        try:
            result = await run_sync(note_service.add_tags, note_id, owner_id, ws_path, tags)
        except (GitError, ValueError, FileNotFoundError) as e:
            raise ToolError(str(e), details={"error": GitErrorCode.GIT_ERROR if isinstance(e, GitError) else None})
        return TagOperationResult.model_validate(result)

    @srv.tool()
    @logged_tool
    async def remove_tag(note_id: str, tags: list[str], ctx: Context) -> TagOperationResult:
        """Usuwa tagi z frontmattera notatki (idempotentnie), bez ruszania treści.
        Tag obecny tylko jako inline #hashtag nie zniknie — wróci jako warning."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e))
        try:
            result = await run_sync(note_service.remove_tags, note_id, owner_id, ws_path, tags)
        except (GitError, ValueError, FileNotFoundError) as e:
            raise ToolError(str(e), details={"error": GitErrorCode.GIT_ERROR if isinstance(e, GitError) else None})
        return TagOperationResult.model_validate(result)

    @srv.tool()
    @logged_tool
    async def set_tags(note_id: str, tags: list[str], ctx: Context, confirm: bool = False) -> str:
        """Nadpisuje frontmatter tagów notatki podaną listą, bez ruszania treści.
        Destrukcyjne: jeśli usunęłoby istniejące tagi, prosi o potwierdzenie (elicitation;
        gdy klient nie wspiera — zwraca requires_confirmation, zawołaj ponownie z confirm=true).
        Zwraca {note_id, tags, frontmatter_tags, warnings}."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e))
        try:
            result = await run_sync(
                note_service.set_tags, note_id, owner_id, ws_path, tags, confirm
            )
        except (GitError, ValueError, FileNotFoundError) as e:
            raise ToolError(str(e), details={"error": GitErrorCode.GIT_ERROR if isinstance(e, GitError) else None})

        async def reapply() -> dict:
            return await run_sync(note_service.set_tags, note_id, owner_id, ws_path, tags, True)

        return await confirm_and_apply(ctx, result, reapply)

    @srv.tool()
    @logged_tool
    async def list_tags(
        ctx: Context,
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
    ) -> list[TagItem]:
        """Zwraca tagi aktywnego workspace z licznikami popularności,
        posortowane malejąco po liczbie notatek. Każdy element: {path, name, count}.
        Użyj do rekonesansu istniejących tagów przed tagowaniem — opcjonalnie
        zawężając do folderu."""
        try:
            owner_id, ws_name, _ = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e))
        tags_result = await run_sync(
            note_service.tag_counts,
            ws_name,
            owner_id=owner_id,
            folder=folder,
            include_subfolders=include_subfolders,
        )
        return [TagItem.model_validate(t) for t in tags_result]

    return srv
