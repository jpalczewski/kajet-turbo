from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.markdown import join_target
from kajet_turbo.mcp.context import (
    ACTIVE_WORKSPACE,
    MCP_CONTEXT,
    ActiveWorkspace,
    active_workspace,
    require_user_id,
)
from kajet_turbo.mcp.notes.types import (
    BatchNoteError,
    BatchNoteSuccess,
    DeletedNoteResult,
    DeleteNotesApplied,
    DeleteNotesRejected,
    EditNotesApplied,
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
from kajet_turbo.mcp.tooling import (
    check_batch,
    publish_note_updated,
    publish_workspace_changed,
    read_tool,
    require_found,
    write_tool,
)
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
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
        await publish_workspace_changed(ws)
        return SavedNoteResult.model_validate(result)

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
        results = await run_sync(
            note_service.save_many,
            ws.owner_id,
            ws.name,
            ws.path,
            [n.model_dump() for n in notes],
        )
        await publish_workspace_changed(ws)
        return [
            BatchNoteSuccess.model_validate(r)
            if "note_id" in r
            else BatchNoteError(index=r["index"], error=r["error"])
            for r in results
        ]

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def get_note(
        note_id: str | None = None,
        title: Annotated[
            str | None,
            Field(
                description="Zamiast note_id: dokładny tytuł notatki, np. '2026-08-22'. "
                "Podaj note_id ALBO title."
            ),
        ] = None,
        folder: Annotated[
            str | None,
            Field(
                description="Zawężenie dla title — jak w wikilinku, czyli *sufiks* ścieżki: "
                "'backlog' trafi w 'kajet-turbo/backlog'. Pominięty = szukaj w całym "
                "workspace. Nie łącz z note_id."
            ),
        ] = None,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> NoteData:
        """Zwraca notatkę jako obiekt ze wszystkimi polami. Błąd gdy notatka nie istnieje.
        To jedyne źródło pełnej, aktualnej treści notatki — search_notes zwraca tylko
        fragmenty (chunki), nie całość; po dokładny tekst zawsze wołaj get_note/get_notes.
        Adresujesz przez note_id albo przez tytuł (+ opcjonalny folder) — to drugie skraca
        typową operację dziennikową do jednego calla. Tytuł pasujący do kilku notatek
        zwraca błąd z listą kandydatów; doprecyzuj folder albo podaj note_id."""
        if note_id is not None:
            if title is not None:
                raise ToolError("Podaj dokładnie jedno: note_id albo title.")
            if folder is not None:
                raise ToolError("folder działa tylko z title — przy note_id go pomiń.")
            return require_found(
                await run_sync(
                    note_service.get_with_content, note_id, owner_id=ws.owner_id, ws_path=ws.path
                ),
                note_id,
            )
        if title is None:
            raise ToolError("Podaj note_id albo title.")
        return require_found(
            await run_sync(
                note_service.get_with_content_by_title,
                title,
                folder,
                owner_id=ws.owner_id,
                ws_name=ws.name,
                ws_path=ws.path,
            ),
            join_target(folder or "", title),
        )

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def get_notes(
        note_ids: list[str],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> list[NoteData | NoteReadError]:
        """Czyta wiele notatek jednym wywołaniem zamiast N x get_note. Max 50 na raz.
        Nieznalezione id → NoteReadError {note_id, error} zamiast przerwania całości."""
        check_batch(note_ids, "note_ids", "note_id")
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
        content: Annotated[
            str | None,
            Field(
                description="New body text for the whole-body modes (overwrite/append/prepend/"
                "replace_section). Omit it to edit only title/tags/folder and leave the body "
                "untouched. Not used by the text modes — those take new_str."
            ),
        ] = None,
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
                description="How to edit the body. Whole-body modes take content: 'overwrite' "
                "(replace the whole body, default), 'append'/'prepend' (add at the end/start of "
                "the body, or of the target_heading section), 'replace_section' (replace the body "
                "of the target_heading section). Text modes take old_str: 'replace_text' (replace "
                "old_str with new_str), 'insert_after' (insert new_str right after the old_str "
                "anchor), 'delete_text' (remove old_str; takes no new_str). Passing a parameter "
                "another mode owns is an error, not a silent no-op."
            ),
        ] = "overwrite",
        target_heading: Annotated[
            str | None,
            Field(
                description="Section heading, e.g. '## Tasks'. Required for replace_section, "
                "optional for append/prepend, unused by every other mode."
            ),
        ] = None,
        old_str: Annotated[
            str | None,
            Field(
                description="Exact text to replace (replace_text), to delete (delete_text), or to "
                "anchor the insertion after (insert_after). Must be unique in the note unless "
                "replace_all is set."
            ),
        ] = None,
        new_str: Annotated[
            str | None,
            Field(
                description="Replacement for old_str (replace_text) or the text to insert after it "
                "(insert_after). Required by both; delete_text takes none."
            ),
        ] = None,
        replace_all: Annotated[
            bool,
            Field(
                description="With replace_text/delete_text: act on EVERY occurrence of old_str "
                "instead of requiring it to be unique. The response carries replaced with the "
                "count."
            ),
        ] = False,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> EditNoteSuccess | StaleVersion:
        """Edit a note. By default (mode='overwrite') it replaces the whole body with content;
        the surgical modes change a fragment without rewriting everything.
        Each mode owns exactly one parameter set: the whole-body modes take content, the text
        modes take old_str (+ new_str, except delete_text). Mixing them is a hard error.
        title/tags/folder can be changed independently of the body edit; passing folder moves
        the note. Omitting content with the default mode edits metadata only.
        content/new_str must carry real newlines (\\n), not literal \\\\n.
        expected_sha is the sha from get_note/get_note_history — proof you saw the current
        version. A mismatch returns StaleVersion: call get_note to re-read the note, then retry
        with the fresh sha."""
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
            old_str=old_str,
            new_str=new_str,
            replace_all=replace_all,
        )
        if result.get("stale_sha"):
            return StaleVersion.model_validate(result)
        await publish_note_updated(ws, result["note_id"])
        return EditNoteSuccess.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    @logged_tool
    async def edit_notes(
        edits: list[NoteEditInput],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> EditNotesApplied | EditNotesRejected:
        """Edit many notes in one atomic commit. All-or-nothing: if ANY edit in the batch is
        invalid (wrong note, broken wikilink, ambiguous target_heading/old_str, duplicate
        note_id, stale expected_sha) the whole batch is rejected and NOTHING is written;
        errors {index, note_id, error} says which item and why.
        Each item takes the same parameter split as edit_note: the whole-body modes take
        content, the text modes take old_str (+ new_str, except delete_text).
        Every item needs expected_sha — the note's sha from get_note/get_note_history,
        proof you saw the current version. On a stale one, call get_note to re-read the
        note and retry.
        Scope: content and tags only — no title/folder changes (use edit_note for those).
        Max 50 edits per call."""
        check_batch(edits, "edits", "edycji")
        result = await run_sync(
            note_service.edit_many,
            ws.owner_id,
            ws.name,
            ws.path,
            [e.model_dump() for e in edits],
        )
        if not result.get("applied"):
            return EditNotesRejected.model_validate(result)
        await publish_workspace_changed(ws)
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
        result = require_found(
            await run_sync(
                note_service.get_outline, note_id, owner_id=ws.owner_id, ws_path=ws.path
            ),
            note_id,
        )
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
        result = await run_sync(
            note_service.move,
            note_id,
            owner_id=ws.owner_id,
            ws_path=ws.path,
            folder=folder,
        )
        await publish_workspace_changed(ws)
        return MovedNoteResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    @logged_tool
    async def delete_note(
        note_id: str,
        expected_sha: Annotated[
            str,
            Field(
                description="Aktualny HEAD sha notatki z get_note/get_note_history — dowód, "
                "że przed usunięciem widziałeś bieżącą wersję. Niezgodność zwraca StaleVersion."
            ),
        ],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> DeletedNoteResult | StaleVersion:
        """Usuwa notatkę. Błąd gdy notatka nie istnieje. Wymaga expected_sha z
        get_note/get_note_history; przy niezgodności zwraca StaleVersion — doczytaj
        aktualną wersję i spróbuj ponownie z nowym sha."""
        result = await run_sync(
            note_service.delete,
            note_id,
            owner_id=ws.owner_id,
            ws_path=ws.path,
            expected_sha=expected_sha,
        )
        if result.get("stale_sha"):
            return StaleVersion.model_validate(result)
        await publish_workspace_changed(ws)
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
        errors {index, note_id, error} per pozycja mówi co. Gating idzie po expected_sha
        — sha ostatniego commita notatki z get_note_history — dowodząc, że wywołujący
        widział bieżącą wersję przed usunięciem. Przy niezgodności zawołaj get_note_history,
        by doczytać aktualną wersję, i spróbuj ponownie. Max 50 usunięć na wywołanie."""
        check_batch(deletes, "deletes", "usunięć")
        result = await run_sync(
            note_service.delete_many,
            ws.owner_id,
            ws.name,
            ws.path,
            [d.model_dump() for d in deletes],
        )
        if not result.get("applied"):
            return DeleteNotesRejected.model_validate(result)
        await publish_workspace_changed(ws)
        return DeleteNotesApplied.model_validate(result)

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def list_notes(
        tags: list[str] | None = None,
        limit: int = 20,
        folder: Annotated[
            str | None,
            Field(
                description="Filter to notes in this folder only, e.g. "
                "'Projekty/Klient A'. Empty string = root."
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
        ctx: Context = MCP_CONTEXT,
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
            # 'all' needs identity only, not a chosen active workspace — activate_workspace()
            # isn't required first.
            owner_id = await require_user_id()
            workspaces = await run_sync(workspace_service.list_accessible, owner_id)
        else:
            ws = await active_workspace(ctx)
            workspaces = [ws_param if ws_param != "active" else ws.name]
            owner_id = ws.owner_id
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
