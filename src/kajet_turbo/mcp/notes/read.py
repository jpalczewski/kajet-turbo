from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.markdown import join_target
from kajet_turbo.mcp.context import ACTIVE_WORKSPACE, ActiveWorkspace
from kajet_turbo.mcp.notes.types import (
    FolderContext,
    FolderExportResult,
    NoteListItem,
    NoteListResponse,
    NoteOutlineResult,
    NoteReadError,
)
from kajet_turbo.mcp.tooling import check_batch, read_tool, require_found
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.services.notes import NoteData, NoteService
from kajet_turbo.workspace import normalize_folder


def build_read(
    note_service: NoteService, folder_meta_repo: FolderMetaRepository, state_store=None
) -> FastMCP:
    srv = FastMCP("notes-read", session_state_store=state_store)

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

    return srv
