from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.context import ACTIVE_WORKSPACE, ActiveWorkspace
from kajet_turbo.mcp.notes.types import (
    StaleVersion,
    TagConflictResult,
    TagItem,
    TagOperationResult,
    TagRenameResult,
)
from kajet_turbo.mcp.tooling import publish_workspace_changed, read_tool, write_tool
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
        result = await run_sync(note_service.add_tags, note_id, ws.owner_id, ws.path, tags)
        await publish_workspace_changed(ws)
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
        result = await run_sync(note_service.remove_tags, note_id, ws.owner_id, ws.path, tags)
        await publish_workspace_changed(ws)
        return TagOperationResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "tags"}, destructive=True))
    @logged_tool
    async def set_tags(
        note_id: str,
        tags: list[str],
        expected_sha: Annotated[
            str,
            Field(
                description="Aktualny HEAD sha notatki z get_note/get_note_history — dowód, "
                "że przed nadpisaniem tagów widziałeś bieżącą wersję. "
                "Niezgodność zwraca StaleVersion."
            ),
        ],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> TagOperationResult | StaleVersion:
        """Nadpisuje frontmatter tagów notatki podaną listą, bez ruszania treści.
        Destrukcyjne (może usunąć istniejące tagi) — wymaga expected_sha z
        get_note/get_note_history; przy niezgodności zwraca StaleVersion — doczytaj
        notatkę i spróbuj ponownie z nowym sha.
        Sukces: TagOperationResult {note_id, tags, frontmatter_tags, warnings}."""
        result = await run_sync(
            note_service.set_tags, note_id, ws.owner_id, ws.path, tags, expected_sha
        )
        if result.get("stale_sha"):
            return StaleVersion.model_validate(result)
        await publish_workspace_changed(ws)
        return TagOperationResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "tags"}, idempotent=True))
    @logged_tool
    async def rename_tag(
        old: str,
        new: str,
        merge: Annotated[
            bool,
            Field(
                description="Zgoda na scalenie, gdy tag `new` już istnieje. Bez niej taki "
                "przypadek zwraca TagConflictResult zamiast cokolwiek zmieniać."
            ),
        ] = False,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> TagRenameResult | TagConflictResult:
        """Zmienia nazwę taga w całym workspace jednym commitem — zamiast N x set_tags.
        Zabiera poddrzewo: 'work' -> 'job' przepisze też 'work/projects' (dopasowanie po
        granicy segmentu, więc 'workflow' zostaje). Przepisuje też inline #hashtagi
        w treści, bo inaczej stary tag wróciłby przy najbliższej synchronizacji.
        Gdy `new` już istnieje, to scalenie — wymaga merge=true, inaczej zwraca
        TagConflictResult z liczbą notatek po obu stronach.
        Bez expected_sha (operacja jest workspace-wide) — cofasz przez git history."""
        result = await run_sync(
            note_service.rename_tag,
            old,
            new,
            owner_id=ws.owner_id,
            ws_name=ws.name,
            ws_path=ws.path,
            merge=merge,
        )
        if result.get("error"):
            return TagConflictResult.model_validate(result)
        if result["renamed"]:
            await publish_workspace_changed(ws)
        return TagRenameResult.model_validate(result)

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
