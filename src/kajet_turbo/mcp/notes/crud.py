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
from kajet_turbo.mcp.notes.types import (
    BatchNoteError,
    BatchNoteSuccess,
    Cancelled,
    ConfirmationRequired,
    DeletedNoteResult,
    DeleteNotesApplied,
    DeleteNotesRejected,
    EditNotesApplied,
    EditNotesConfirmationRequired,
    EditNotesRejected,
    EditNoteSuccess,
    FolderContext,
    FolderExportResult,
    GrepMatch,
    GrepResult,
    MovedNoteResult,
    NoteDeleteInput,
    NoteEditInput,
    NoteInput,
    NoteListItem,
    NoteListResponse,
    NoteOutlineResult,
    NoteReadError,
    ReindexResult,
    SavedNoteResult,
    SearchChunkResult,
    StaleVersion,
)
from kajet_turbo.mcp.tooling import read_tool, write_tool
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.repositories.git import GitError
from kajet_turbo.services.notes import NoteData, NoteService
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.workspace import normalize_folder


def build_crud(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    folder_meta_repo: FolderMetaRepository,
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
            "workspace_changed",
            WorkspaceChangedEvent(
                type="workspace_changed",
                owner_id=ws.owner_id,
                workspace=ws.name,
            ).model_dump(),
        )
        return SavedNoteResult(note_id=result["note_id"])

    @srv.tool(**write_tool(tags={"notes", "crud"}))
    @logged_tool
    async def save_notes(
        notes: list[NoteInput],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> list[BatchNoteSuccess | BatchNoteError]:
        """Zapisuje wiele notatek naraz (jeden commit, równoległe indeksowanie).
        Użyj tego narzędzia zawsze, gdy dodajesz 2+ notatek — zamiast wielu wywołań
        save_note. Best-effort: każda notatka walidowana osobno; wynik per-note to
        BatchNoteSuccess {index, note_id} lub BatchNoteError {index, error}.
        Wikilinki do notatek z tego samego batcha rozwiązują się niezależnie
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
        return [
            BatchNoteSuccess(index=r["index"], note_id=r["note_id"])
            if "note_id" in r
            else BatchNoteError(index=r["index"], error=r["error"])
            for r in results
        ]

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def get_note(
        note_id: str,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> NoteData:
        """Zwraca notatkę jako obiekt ze wszystkimi polami. Błąd gdy notatka nie istnieje.
        To jedyne źródło pełnej, aktualnej treści notatki — search_notes zwraca tylko
        fragmenty (chunki), nie całość; po dokładny tekst zawsze wołaj get_note/get_notes."""
        result = await run_sync(
            note_service.get_with_content, note_id, owner_id=ws.owner_id, ws_path=ws.path
        )
        if result is None:
            raise ToolError(f"Notatka {note_id} nie znaleziona.")
        return result

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def get_notes(
        note_ids: list[str],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> list[NoteData | NoteReadError]:
        """Czyta wiele notatek jednym wywołaniem zamiast N x get_note. Max 50 na raz.
        Nieznalezione id → NoteReadError {note_id, error} zamiast przerwania całości."""
        if not note_ids:
            raise ToolError("note_ids nie może być puste.")
        if len(note_ids) > 50:
            raise ToolError(f"Maksymalnie 50 note_id na wywołanie (podano {len(note_ids)}).")
        results = await run_sync(
            note_service.get_many, note_ids, owner_id=ws.owner_id, ws_path=ws.path
        )
        return [r if isinstance(r, NoteData) else NoteReadError.model_validate(r) for r in results]

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    @logged_tool
    async def edit_note(
        note_id: str,
        expected_sha: Annotated[
            str,
            Field(
                description="Aktualny HEAD sha notatki z get_note/get_note_history — dowód, że "
                "przed edycją widziałeś bieżącą wersję. Niezgodność odrzuca edycję."
            ),
        ],
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
        replace_all: Annotated[
            bool,
            Field(
                description="Z trybem replace_text/delete_text: podmień/usuń WSZYSTKIE "
                "wystąpienia old_text (nie tylko unikalne). Zwrot niesie replaced z liczbą "
                "podmian."
            ),
        ] = False,
        confirm: bool = Field(
            False,
            description="Potwierdzenie destrukcyjnego nadpisania "
            "(utrata tagów / nadpisanie treści).",
        ),
        ctx: Context = MCP_CONTEXT,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> EditNoteSuccess | ConfirmationRequired | Cancelled | StaleVersion:
        """Edytuje notatkę. Domyślnie (mode='overwrite') podmienia całe body na content;
        tryby chirurgiczne pozwalają dopisać/podmienić fragment bez przepisywania całości.
        folder opcjonalny — jeśli podany, przenosi notatkę do nowego folderu.
        title/tags/folder można zmieniać niezależnie od trybu edycji content.
        content powinien zawierać rzeczywiste znaki nowej linii (\\n), nie literalne \\\\n.
        expected_sha to sha z get_note/get_note_history — dowód, że widziałeś bieżącą wersję;
        niezgodność zwraca StaleVersion — zawołaj get_note, by doczytać aktualną treść, i spróbuj
        ponownie z nowym sha.
        Nadpisanie niepustej treści lub utrata tagów wymagają potwierdzenia — elicitation gdy
        klient wspiera, inaczej zwraca ConfirmationRequired; zawołaj ponownie z confirm=true.
        replace_all=true z replace_text/delete_text podmienia/usuwa każde wystąpienie
        old_text (zamiast wymagać unikalności) i zwraca replaced z liczbą podmian."""
        try:
            result = await run_sync(
                note_service.update,
                note_id,
                owner_id=ws.owner_id,
                ws_path=ws.path,
                expected_sha=expected_sha,
                title=title,
                content=content,
                tags=tags,
                folder=folder,
                mode=mode,
                target_heading=target_heading,
                old_text=old_text,
                confirm=confirm,
                replace_all=replace_all,
            )
        except (ValueError, FileNotFoundError, FileExistsError) as e:
            raise ToolError(str(e)) from e
        except GitError as e:
            raise ToolError(str(e)) from e

        if result.get("stale_sha"):
            return StaleVersion.model_validate(result)

        async def reapply() -> dict:
            return await run_sync(
                note_service.update,
                note_id,
                owner_id=ws.owner_id,
                ws_path=ws.path,
                expected_sha=expected_sha,
                title=title,
                content=content,
                tags=tags,
                folder=folder,
                mode=mode,
                target_heading=target_heading,
                old_text=old_text,
                confirm=True,
                replace_all=replace_all,
            )

        data = await confirm_and_apply(ctx, result, reapply)
        if data.get("requires_confirmation"):
            return ConfirmationRequired.model_validate(data)
        if data.get("cancelled"):
            return Cancelled.model_validate(data)
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
        return EditNoteSuccess.model_validate(data)

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    @logged_tool
    async def edit_notes(
        edits: list[NoteEditInput],
        confirm: bool = False,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> EditNotesApplied | EditNotesRejected | EditNotesConfirmationRequired:
        """Edytuje wiele notatek w jednym atomowym commicie. All-or-nothing: jeśli
        KTÓRAKOLWIEK edycja w batchu jest niepoprawna (zła notatka, błędny wikilink,
        niejednoznaczny target_heading/old_text, duplikat note_id, nieaktualny
        expected_sha) — cały batch jest odrzucany i NIC nie jest zapisywane;
        errors {index, note_id, error} per pozycja mówi co. Każda pozycja
        wymaga expected_sha — sha notatki z get_note/get_note_history — dowodu, że
        widziałeś bieżącą wersję przed edycją. Przy nieaktualnym expected_sha zawołaj
        get_note, by doczytać aktualną treść, i spróbuj ponownie.
        Zakres: tylko content i tagi — bez zmiany title/folder (do tego użyj edit_note).
        Operacje destrukcyjne (utrata tagów, nadpisanie treści w mode='overwrite')
        wymagają confirm=true — bez elicitation per-item; sprawdź
        requires_confirmation w odpowiedzi, potwierdź z użytkownikiem i zawołaj
        ponownie z confirm=true dla całego batcha. Max 50 edycji na wywołanie."""
        if not edits:
            raise ToolError("edits nie może być puste.")
        if len(edits) > 50:
            raise ToolError(f"Maksymalnie 50 edycji na wywołanie (podano {len(edits)}).")
        try:
            result = await run_sync(
                note_service.edit_many,
                ws.owner_id,
                ws.name,
                ws.path,
                [e.model_dump() for e in edits],
                confirm=confirm,
            )
        except GitError as e:
            raise ToolError(str(e)) from e
        if result.get("requires_confirmation"):
            return EditNotesConfirmationRequired.model_validate(result)
        if not result.get("applied"):
            return EditNotesRejected.model_validate(result)
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
        return EditNotesApplied.model_validate(result)

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def get_note_outline(
        note_id: str,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> NoteOutlineResult:
        """Zwraca strukturę notatki (nagłówki + rozmiary sekcji) bez treści — do
        chirurgicznej edycji bez wciągania całej karty w kontekst. target_heading
        każdej sekcji wklej bezpośrednio do edit_note(mode='replace_section',
        target_heading=...). ambiguous=true → ten nagłówek powtarza się w dokumencie,
        target_heading nie zadziała (edit_note zwróci błąd niejednoznaczności) — użyj
        wtedy innego trybu (np. replace_text)."""
        result = await run_sync(
            note_service.get_outline, note_id, owner_id=ws.owner_id, ws_path=ws.path
        )
        if result is None:
            raise ToolError(f"Notatka {note_id} nie znaleziona.")
        return NoteOutlineResult.model_validate(result)

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

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    @logged_tool
    async def delete_notes(
        deletes: list[NoteDeleteInput],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> DeleteNotesApplied | DeleteNotesRejected:
        """Usuwa wiele notatek w jednym atomowym commicie. All-or-nothing: jeśli
        KTÓRAKOLWIEK pozycja w batchu jest niepoprawna (zła notatka, duplikat note_id,
        nieaktualny expected_sha) — cały batch jest odrzucany i NIC nie jest usuwane;
        errors {index, note_id, error} per pozycja mówi co. Zamiast confirm
        gating idzie po expected_sha — sha ostatniego commita notatki z get_note_history —
        dowodząc, że wywołujący widział bieżącą wersję przed usunięciem. Przy niezgodności
        zawołaj get_note_history, by doczytać aktualną wersję, i spróbuj ponownie. Max 50
        usunięć na wywołanie."""
        if not deletes:
            raise ToolError("deletes nie może być puste.")
        if len(deletes) > 50:
            raise ToolError(f"Maksymalnie 50 usunięć na wywołanie (podano {len(deletes)}).")
        result = await run_sync(
            note_service.delete_many,
            ws.owner_id,
            ws.name,
            ws.path,
            [d.model_dump() for d in deletes],
        )
        if not result.get("applied"):
            return DeleteNotesRejected.model_validate(result)
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
        return DeleteNotesApplied.model_validate(result)

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def list_notes(
        tags: list[str] | None = None,
        limit: int = 20,
        folder: Annotated[
            str | None,
            Field(
                description="Filter to notes in this folder only, e.g. 'Projekty/Klient A'. Empty string = root."
            ),
        ] = None,
        sort: Annotated[
            Literal["default", "updated", "title", "created"],
            Field(
                description="'default' — recency globally, README-first natural title order "
                "inside a folder. 'updated'/'created' — always that recency order, even inside "
                "a folder. 'title' — natural title order (README-first), even globally."
            ),
        ] = "default",
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> NoteListResponse:
        """Zwraca listę notatek wraz z metadanymi folderu (jeśli ustawione).
        folder: opcjonalny filtr — tylko notatki z tego folderu (np. 'Projekty/Klient A').
        Filtr tags używa OR i jest hierarchiczny: podanie 'work' dopasuje też notatki
        otagowane 'work/projects' itd. (dopasowanie po prefiksie segmentów).
        folder_context w odpowiedzi zawiera instructions dla LLM-a, gdy są ustawione dla folderu."""
        notes = await run_sync(
            note_service.list_notes,
            ws.name,
            owner_id=ws.owner_id,
            tags=tags or None,
            limit=limit,
            folder=folder,
            sort=sort,
        )
        folder_context: FolderContext | None = None
        if folder is not None:
            meta = await run_sync(
                folder_meta_repo.get, ws.owner_id, ws.name, normalize_folder(folder)
            )
            if meta is not None:
                folder_context = FolderContext.model_validate(meta)
        return NoteListResponse(
            notes=[NoteListItem.model_validate(n) for n in notes],
            folder_context=folder_context,
        )

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def export_folder(
        folder: str,
        max_chars: int = 80_000,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> FolderExportResult:
        """Eksportuje cały folder (rekursywnie, z podfolderami) jako jeden dokument
        markdown — do analizy korpusu N powiązanych notatek naraz, zamiast N osobnych
        wywołań get_note. Przy przekroczeniu max_chars ucina na granicy notatki (nigdy
        w środku); pominięte notatki wraca omitted. Pierwsza notatka jest zawsze
        w całości, nawet gdy sama przekracza max_chars."""
        result = await run_sync(
            note_service.export_folder,
            ws.name,
            owner_id=ws.owner_id,
            ws_path=ws.path,
            folder=folder,
            max_chars=max_chars,
        )
        return FolderExportResult.model_validate(result)

    @srv.tool(**read_tool(tags={"notes", "search"}))
    @logged_tool
    async def search_notes(
        query: str,
        workspace: str = "active",
        limit: int = 10,
        folder: Annotated[
            str | None,
            Field(
                description="Zawęź wyszukiwanie do notatek w tym folderze i podfolderach, "
                "np. 'Projekty/Klient A'."
            ),
        ] = None,
        tags: Annotated[
            list[str] | None,
            Field(
                description="Zawęź wyszukiwanie do notatek z tymi tagami (OR, hierarchiczne — "
                "jak w list_notes)."
            ),
        ] = None,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> list[SearchChunkResult]:
        """Szuka notatek (chunk-level hybrid: FTS + semantic + dokładne dopasowanie
        tytułu/tagu/folderu).
        workspace='active' (domyślnie) — szuka tylko w aktywnym workspace.
        workspace='all' — szuka we wszystkich dostępnych workspace'ach (cross-workspace).
        folder/tags zawężają wyszukiwanie do podzbioru notatek (przecięcie, gdy oba podane).
        Zwraca fragmenty (chunki): {note_id, title, folder, updated_at, header_path, content,
        score, matched_on}. matched_on obecne, gdy trafienie pochodzi z dokładnego
        dopasowania tytułu/tagu/folderu (nie tylko z rankingu FTS/semantycznego).
        NIE zwraca pełnej notatki, nawet przy dokładnym trafieniu tytułu — to zawsze
        fragment. Gdy potrzebujesz całej, aktualnej treści konkretnej notatki, użyj
        search_notes tylko żeby znaleźć note_id, a samą treść pobierz przez get_note
        (jedna) lub get_notes (kilka) — nigdy nie traktuj chunka jako całości notatki.
        note_id z innych workspace'ów możesz linkować przez [[note:NOTE_ID]].
        Pusty [] gdy brak wyników."""
        ws_param = workspace or "active"
        if ws_param == "all":
            workspaces = await run_sync(workspace_service.list_accessible, ws.user_id)
        else:
            workspaces = [ws_param if ws_param != "active" else ws.name]
        # search_async borrows a run_sync slot only for the ms-scale DB phases; the
        # query-embedding HTTP call is awaited natively on the event loop.
        results = await note_service.search_async(
            query,
            workspaces,
            owner_id=ws.owner_id,
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
        try:
            result = await run_sync(
                note_service.grep,
                ws.name,
                ws.path,
                pattern,
                folder=folder,
                case_sensitive=case_sensitive,
                max_results=max_results,
            )
        except ValueError as e:
            raise ToolError(str(e)) from e
        return GrepResult(
            matches=[GrepMatch.model_validate(m) for m in result["matches"]],
            truncated=result["truncated"],
        )

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
