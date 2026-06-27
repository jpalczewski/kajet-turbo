import json
import time
from typing import Annotated, Literal

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from kajet_turbo.api.schemas.ws import NoteUpdatedEvent, WorkspaceChangedEvent
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import event_repo
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.context import ACTIVE_WORKSPACE, MCP_CONTEXT, ActiveWorkspace
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
from kajet_turbo.mcp.tooling import read_tool, write_tool
from kajet_turbo.repositories.git import GitError
from kajet_turbo.services.notes import NoteData, NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def build_crud(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    state_store=None,
) -> FastMCP:
    srv = FastMCP("notes-crud", session_state_store=state_store)

    @srv.tool(**write_tool(tags={"notes", "crud"}))
    @logged_tool
    async def save_note(
        title: str,
        content: str,
        tags: list[str] | None = None,
        folder: str = "",
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> SavedNoteResult:
        """Zapisuje nową notatkę w podanym folderze (domyślnie root).
        folder: opcjonalna ścieżka np. 'Projekty/Klient A'.
        Uwaga: content powinien zawierać rzeczywiste znaki nowej linii (\\n),
        nie literalne \\\\n."""
        try:
            result = await run_sync(
                note_service.save,
                ws.owner_id,
                ws.name,
                ws.path,
                title,
                content,
                tags or [],
                folder=folder,
            )
        except (GitError, ValueError) as e:
            raise ToolError(str(e)) from e
        await run_sync(
            event_repo.publish,
            ws.owner_id,
            "note_updated",
            NoteUpdatedEvent(
                type="note_updated",
                owner_id=ws.owner_id,
                workspace=ws.name,
                note_id=result["note_id"],
                updated_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            ).model_dump(),
        )
        return SavedNoteResult(note_id=result["note_id"])

    @srv.tool(**write_tool(tags={"notes", "crud"}))
    @logged_tool
    async def save_notes(
        notes: list[NoteInput],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> str:
        """Zapisuje wiele notatek naraz (jeden commit, równoległe indeksowanie).
        Użyj tego narzędzia zawsze, gdy dodajesz 2+ notatek — zamiast wielu wywołań
        save_note. Best-effort: każda notatka walidowana osobno; wynik to lista
        [{"index": i, "note_id": "..."} | {"index": i, "error": "..."}] w kolejności
        wejścia. Wikilinki do notatek z tego samego batcha rozwiązują się niezależnie
        od kolejności. content z prawdziwymi znakami nowej linii (\\n), nie literalnymi \\\\n."""
        try:
            results = await run_sync(
                note_service.save_many,
                ws.owner_id,
                ws.name,
                ws.path,
                [n.model_dump() for n in notes],
            )
        except GitError as e:
            raise ToolError(str(e)) from e
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        for item in results:
            if "note_id" in item:
                await run_sync(
                    event_repo.publish,
                    ws.owner_id,
                    "note_updated",
                    NoteUpdatedEvent(
                        type="note_updated",
                        owner_id=ws.owner_id,
                        workspace=ws.name,
                        note_id=item["note_id"],
                        updated_at=now,
                    ).model_dump(),
                )
        return json.dumps(results, ensure_ascii=False)

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def get_note(
        note_id: str,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> NoteData:
        """Zwraca notatkę jako obiekt ze wszystkimi polami. Błąd gdy notatka nie istnieje."""
        result = await run_sync(
            note_service.get_with_content, note_id, owner_id=ws.owner_id, ws_path=ws.path
        )
        if result is None:
            raise ToolError(f"Notatka {note_id} nie znaleziona.")
        return result

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    @logged_tool
    async def edit_note(
        note_id: str,
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
        ctx: Context = MCP_CONTEXT,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
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
            result = await run_sync(
                note_service.update,
                note_id,
                owner_id=ws.owner_id,
                ws_path=ws.path,
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
            raise ToolError(str(e)) from e
        except GitError as e:
            raise ToolError(str(e)) from e

        async def reapply() -> dict:
            return await run_sync(
                note_service.update,
                note_id,
                owner_id=ws.owner_id,
                ws_path=ws.path,
                title=title,
                content=content,
                tags=tags,
                folder=folder,
                mode=mode,
                target_heading=target_heading,
                old_text=old_text,
                confirm=True,
            )

        applied = await confirm_and_apply(ctx, result, reapply)
        data = json.loads(applied)
        if (
            "note_id" in data
            and not data.get("requires_confirmation")
            and not data.get("cancelled")
        ):
            await run_sync(
                event_repo.publish,
                ws.owner_id,
                "note_updated",
                NoteUpdatedEvent(
                    type="note_updated",
                    owner_id=ws.owner_id,
                    workspace=ws.name,
                    note_id=data["note_id"],
                    updated_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                ).model_dump(),
            )
        return applied

    @srv.tool(**write_tool(tags={"notes", "crud"}))
    @logged_tool
    async def move_note(
        note_id: str,
        folder: str,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> MovedNoteResult:
        """Przenosi notatkę do folderu w aktywnym workspace, tworząc brakującą ścieżkę.
        folder: pełna ścieżka folderu lub pusty string dla root."""
        try:
            result = await run_sync(
                note_service.move,
                note_id,
                owner_id=ws.owner_id,
                ws_path=ws.path,
                folder=folder,
            )
        except (ValueError, FileNotFoundError, FileExistsError, GitError) as e:
            raise ToolError(str(e)) from e
        await run_sync(
            event_repo.publish,
            ws.owner_id,
            "workspace_changed",
            WorkspaceChangedEvent(
                type="workspace_changed",
                owner_id=ws.owner_id,
                workspace=ws.name,
            ).model_dump(),
        )
        return MovedNoteResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    @logged_tool
    async def delete_note(
        note_id: str,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> DeletedNoteResult:
        """Usuwa notatkę. Błąd gdy notatka nie istnieje."""
        try:
            await run_sync(note_service.delete, note_id, owner_id=ws.owner_id, ws_path=ws.path)
        except ValueError as e:
            raise ToolError(str(e)) from e
        await run_sync(
            event_repo.publish,
            ws.owner_id,
            "workspace_changed",
            WorkspaceChangedEvent(
                type="workspace_changed",
                owner_id=ws.owner_id,
                workspace=ws.name,
            ).model_dump(),
        )
        return DeletedNoteResult(note_id=note_id)

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def list_notes(
        tags: list[str] | None = None,
        limit: int = 20,
        folder: str | None = None,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> list[NoteListItem]:
        """Zwraca listę notatek. Każda notatka zawiera pole 'folder'.
        folder: opcjonalny filtr — tylko notatki z tego folderu (np. 'Projekty/Klient A').
        Filtr tags używa OR i jest hierarchiczny: podanie 'work' dopasuje też notatki
        otagowane 'work/projects' itd. (dopasowanie po prefiksie segmentów)."""
        notes = await run_sync(
            note_service.list_notes,
            ws.name,
            owner_id=ws.owner_id,
            tags=tags or None,
            limit=limit,
            folder=folder,
        )
        return [NoteListItem.model_validate(n) for n in notes]

    @srv.tool(**read_tool(tags={"notes", "search"}))
    @logged_tool
    async def search_notes(
        query: str,
        workspace: str = "active",
        limit: int = 10,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> list[SearchChunkResult]:
        """Szuka notatek (chunk-level hybrid: FTS + semantic). workspace='active'
        (domyślnie) lub 'all'. Zwraca fragmenty (chunki):
        {note_id, title, folder, header_path, content, score}.
        Pusty [] gdy brak wyników."""
        ws_param = workspace or "active"
        if ws_param == "all":
            workspaces = await run_sync(workspace_service.list_accessible, ws.user_id)
        else:
            workspaces = [ws_param if ws_param != "active" else ws.name]
        results = await run_sync(
            note_service.search, query, workspaces, owner_id=ws.owner_id, limit=limit
        )
        return [SearchChunkResult.model_validate(r) for r in results]

    @srv.tool(**write_tool(tags={"notes", "index"}, idempotent=True))
    @logged_tool
    async def reindex_workspace(
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> ReindexResult:
        """Przebudowuje indeks SQLite z plików .md w aktywnym workspace."""
        result = await run_sync(
            note_service.reindex, ws.name, owner_id=ws.owner_id, ws_path=ws.path
        )
        return ReindexResult.model_validate(result)

    return srv
