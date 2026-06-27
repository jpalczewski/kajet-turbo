from typing import Annotated, Literal

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.errors import GitError as GitErrorCode
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.notes._helpers import confirm_and_apply
from kajet_turbo.mcp.notes._types import (
    DeletedNoteResult,
    MovedNoteResult,
    NoteInput,
    NoteListItem,
    ReindexResult,
    SavedNoteResult,
    SearchChunkResult,
)
from kajet_turbo.mcp.workspaces import get_active_workspace
from kajet_turbo.repositories.git import GitError
from kajet_turbo.services.notes import NoteData, NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def build_crud(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    state_store=None,
) -> FastMCP:
    srv = FastMCP("notes-crud", session_state_store=state_store)

    @srv.tool()
    @logged_tool
    async def save_note(
        title: str,
        content: str,
        ctx: Context,
        tags: list[str] | None = None,
        folder: str = "",
    ) -> SavedNoteResult:
        """Zapisuje nową notatkę w podanym folderze (domyślnie root).
        folder: opcjonalna ścieżka np. 'Projekty/Klient A'.
        Uwaga: content powinien zawierać rzeczywiste znaki nowej linii (\\n),
        nie literalne \\\\n."""
        try:
            owner_id, ws_name, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e))
        try:
            result = await run_sync(
                note_service.save,
                owner_id,
                ws_name,
                ws_path,
                title,
                content,
                tags or [],
                folder=folder,
            )
        except (GitError, ValueError) as e:
            raise ToolError(str(e), details={"error": GitErrorCode.GIT_ERROR if isinstance(e, GitError) else None})
        return SavedNoteResult(note_id=result["note_id"])

    @srv.tool()
    @logged_tool
    async def save_notes(notes: list[NoteInput], ctx: Context) -> str:
        """Zapisuje wiele notatek naraz (jeden commit, równoległe indeksowanie).
        Użyj tego narzędzia zawsze, gdy dodajesz 2+ notatek — zamiast wielu wywołań
        save_note. Best-effort: każda notatka walidowana osobno; wynik to lista
        [{"index": i, "note_id": "..."} | {"index": i, "error": "..."}] w kolejności
        wejścia. Wikilinki do notatek z tego samego batcha rozwiązują się niezależnie
        od kolejności. content z prawdziwymi znakami nowej linii (\\n), nie literalnymi \\\\n."""
        try:
            owner_id, ws_name, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e))
        try:
            results = await run_sync(
                note_service.save_many,
                owner_id,
                ws_name,
                ws_path,
                [n.model_dump() for n in notes],
            )
        except GitError as e:
            raise ToolError(str(e), details={"error": GitErrorCode.GIT_ERROR})
        import json
        return json.dumps(results, ensure_ascii=False)

    @srv.tool()
    @logged_tool
    async def get_note(note_id: str, ctx: Context) -> NoteData:
        """Zwraca notatkę jako obiekt ze wszystkimi polami. Błąd gdy notatka nie istnieje."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e))
        result = await run_sync(
            note_service.get_with_content, note_id, owner_id=owner_id, ws_path=ws_path
        )
        if result is None:
            raise ToolError(f"Notatka {note_id} nie znaleziona.")
        return result

    @srv.tool()
    @logged_tool
    async def edit_note(
        note_id: str,
        ctx: Context,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        folder: str | None = None,
        mode: Annotated[
            Literal[
                "overwrite",
                "append",
                "prepend",
                "replace_section",
                "replace_text",
                "insert_after",
                "delete_text",
            ],
            Field(
                description="Tryb edycji pola content: 'overwrite' (podmień całe body, domyślny), "
                "'append'/'prepend' (dopisz na koniec/początek body lub sekcji target_heading), "
                "'replace_section' (podmień body sekcji target_heading), "
                "'replace_text' (exact match: podmień unikalny old_text na content), "
                "'insert_after' (wstaw content zaraz po unikalnej kotwicy old_text), "
                "'delete_text' (usuń unikalny old_text — bez podawania content)."
            ),
        ] = "overwrite",
        target_heading: Annotated[
            str | None,
            Field(
                description="Nagłówek sekcji, np. '## Zadania'. "
                "Wymagany dla replace_section, opcjonalny dla append/prepend."
            ),
        ] = None,
        old_text: Annotated[
            str | None,
            Field(
                description="Dokładny tekst do podmiany (replace_text), usunięcia (delete_text) "
                "lub kotwica, po której wstawić content (insert_after). "
                "Musi być unikalny w notatce."
            ),
        ] = None,
        confirm: bool = Field(
            False,
            description="Potwierdzenie destrukcyjnego nadpisania "
            "(utrata tagów / nadpisanie treści).",
        ),
    ) -> str:
        """Edytuje notatkę. Domyślnie (mode='overwrite') podmienia całe body na content;
        tryby chirurgiczne pozwalają dopisać/podmienić fragment bez przepisywania całości.
        folder opcjonalny — jeśli podany, przenosi notatkę do nowego folderu.
        title/tags/folder można zmieniać niezależnie od trybu edycji content.
        content powinien zawierać rzeczywiste znaki nowej linii (\\n), nie literalne \\\\n.
        Nadpisanie niepustej treści lub utrata tagów wymagają potwierdzenia — elicitation gdy
        klient wspiera, inaczej zwraca requires_confirmation=true; zawołaj ponownie z confirm=true.
        Sukces: {"note_id": "..."}."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e))
        try:
            result = await run_sync(
                note_service.update,
                note_id,
                owner_id=owner_id,
                ws_path=ws_path,
                title=title,
                content=content,
                tags=tags,
                folder=folder,
                mode=mode,
                target_heading=target_heading,
                old_text=old_text,
                confirm=confirm,
            )
        except (ValueError, FileNotFoundError, FileExistsError) as e:
            raise ToolError(str(e))
        except GitError as e:
            raise ToolError(str(e), details={"error": GitErrorCode.GIT_ERROR})

        async def reapply() -> dict:
            return await run_sync(
                note_service.update,
                note_id,
                owner_id=owner_id,
                ws_path=ws_path,
                title=title,
                content=content,
                tags=tags,
                folder=folder,
                mode=mode,
                target_heading=target_heading,
                old_text=old_text,
                confirm=True,
            )

        return await confirm_and_apply(ctx, result, reapply)

    @srv.tool()
    @logged_tool
    async def move_note(note_id: str, folder: str, ctx: Context) -> MovedNoteResult:
        """Przenosi notatkę do folderu w aktywnym workspace, tworząc brakującą ścieżkę.
        folder: pełna ścieżka folderu lub pusty string dla root."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e))
        try:
            result = await run_sync(
                note_service.move,
                note_id,
                owner_id=owner_id,
                ws_path=ws_path,
                folder=folder,
            )
        except (ValueError, FileNotFoundError, FileExistsError, GitError) as e:
            raise ToolError(str(e))
        return MovedNoteResult.model_validate(result)

    @srv.tool()
    @logged_tool
    async def delete_note(note_id: str, ctx: Context) -> DeletedNoteResult:
        """Usuwa notatkę. Błąd gdy notatka nie istnieje."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e))
        try:
            await run_sync(note_service.delete, note_id, owner_id=owner_id, ws_path=ws_path)
        except ValueError as e:
            raise ToolError(str(e))
        return DeletedNoteResult(note_id=note_id)

    @srv.tool()
    @logged_tool
    async def list_notes(
        ctx: Context,
        tags: list[str] | None = None,
        limit: int = 20,
        folder: str | None = None,
    ) -> list[NoteListItem]:
        """Zwraca listę notatek. Każda notatka zawiera pole 'folder'.
        folder: opcjonalny filtr — tylko notatki z tego folderu (np. 'Projekty/Klient A').
        Filtr tags używa OR i jest hierarchiczny: podanie 'work' dopasuje też notatki
        otagowane 'work/projects' itd. (dopasowanie po prefiksie segmentów)."""
        try:
            owner_id, ws_name, _ = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e))
        notes = await run_sync(
            note_service.list_notes,
            ws_name,
            owner_id=owner_id,
            tags=tags or None,
            limit=limit,
            folder=folder,
        )
        return [NoteListItem.model_validate(n) for n in notes]

    @srv.tool()
    @logged_tool
    async def search_notes(
        query: str,
        ctx: Context,
        workspace: str = "active",
        limit: int = 10,
    ) -> list[SearchChunkResult]:
        """Szuka notatek (chunk-level hybrid: FTS + semantic). workspace='active'
        (domyślnie) lub 'all'. Zwraca fragmenty (chunki):
        {note_id, title, folder, header_path, content, score}.
        Pusty [] gdy brak wyników."""
        try:
            owner_id, active_ws, _ = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e))
        real_user_id: str | None = await ctx.get_state("active_user_id")
        ws_param = workspace or "active"
        if ws_param == "all":
            workspaces = await run_sync(workspace_service.list_accessible, real_user_id)
        else:
            workspaces = [ws_param if ws_param != "active" else active_ws]
        results = await run_sync(
            note_service.search, query, workspaces, owner_id=owner_id, limit=limit
        )
        return [SearchChunkResult.model_validate(r) for r in results]

    @srv.tool()
    @logged_tool
    async def reindex_workspace(ctx: Context) -> ReindexResult:
        """Przebudowuje indeks SQLite z plików .md w aktywnym workspace."""
        try:
            owner_id, ws_name, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            raise ToolError(str(e))
        result = await run_sync(note_service.reindex, ws_name, owner_id=owner_id, ws_path=ws_path)
        return ReindexResult.model_validate(result)

    return srv
