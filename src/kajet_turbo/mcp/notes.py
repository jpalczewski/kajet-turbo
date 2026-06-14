import json
from typing import Annotated, Literal

from fastmcp import Context, FastMCP
from fastmcp.server.elicitation import AcceptedElicitation
from mcp.types import ClientCapabilities, ElicitationCapability
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.workspaces import get_active_workspace
from kajet_turbo.repositories.git import GitError
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def _client_supports_elicitation(ctx: Context) -> bool:
    """Whether the connected MCP client advertised the elicitation capability."""
    try:
        return ctx.session.check_client_capability(
            ClientCapabilities(elicitation=ElicitationCapability())
        )
    except Exception:
        return False


async def _confirm_and_apply(ctx: Context, result: dict, reapply) -> str:
    """Resolve a possibly-destructive service result.

    If ``result`` asks for confirmation, ask the human via elicitation when the client
    supports it and re-run ``reapply`` (the op with confirm=True) on accept; otherwise
    return the payload so the model can relay it and re-call with confirm=true.
    """
    if not result.get("requires_confirmation"):
        return json.dumps(result, ensure_ascii=False)
    if _client_supports_elicitation(ctx):
        elicited = await ctx.elicit(result["warning"], response_type=["potwierdzam", "anuluj"])
        if isinstance(elicited, AcceptedElicitation) and elicited.data == "potwierdzam":
            return json.dumps(await reapply(), ensure_ascii=False)
        return json.dumps(
            {
                "note_id": result["note_id"],
                "cancelled": True,
                "message": "Anulowano — nic nie zmieniono.",
            },
            ensure_ascii=False,
        )
    return json.dumps(result, ensure_ascii=False)


def register_notes(
    mcp: FastMCP, note_service: NoteService, workspace_service: WorkspaceService
) -> None:
    @mcp.tool()
    @logged_tool
    async def save_note(
        title: str,
        content: str,
        ctx: Context,
        tags: list[str] | None = None,
        folder: str = "",
    ) -> str:
        """Zapisuje nową notatkę w podanym folderze (domyślnie root).
        folder: opcjonalna ścieżka np. 'Projekty/Klient A'.
        Sukces: {"note_id": "..."}. Błąd: {"error": "..."}.
        Uwaga: content powinien zawierać rzeczywiste znaki nowej linii (\\n),
        nie literalne \\\\n."""
        try:
            owner_id, ws_name, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
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
            return json.dumps({"error": str(e)})
        return json.dumps(result)

    @mcp.tool()
    @logged_tool
    async def get_note(note_id: str, ctx: Context) -> str:
        """Zwraca notatkę jako JSON object. Błąd: {"error": "..."}."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        result = await run_sync(
            note_service.get_with_content, note_id, owner_id=owner_id, ws_path=ws_path
        )
        if result is None:
            return json.dumps({"error": f"Notatka {note_id} nie znaleziona."})
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
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
                "overwrite", "append", "prepend", "replace_section", "replace_text", "insert_after"
            ],
            Field(
                description="Tryb edycji pola content: 'overwrite' (podmień całe body, domyślny), "
                "'append'/'prepend' (dopisz na koniec/początek body lub sekcji target_heading), "
                "'replace_section' (podmień body sekcji target_heading), "
                "'replace_text' (exact match: podmień unikalny old_text na content), "
                "'insert_after' (wstaw content zaraz po unikalnej kotwicy old_text)."
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
                description="Dokładny tekst do podmiany (replace_text) lub kotwica, po której "
                "wstawić content (insert_after). Musi być unikalny w notatce."
            ),
        ] = None,
    ) -> str:
        """Edytuje notatkę. Domyślnie (mode='overwrite') podmienia całe body na content;
        tryby chirurgiczne pozwalają dopisać/podmienić fragment bez przepisywania całości.
        folder opcjonalny — jeśli podany, przenosi notatkę do nowego folderu.
        title/tags/folder można zmieniać niezależnie od trybu edycji content.
        content powinien zawierać rzeczywiste znaki nowej linii (\\n), nie literalne \\\\n.
        Sukces: {"note_id": "..."}. Błąd: {"error": "..."}."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
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
            )
        except (ValueError, FileNotFoundError, FileExistsError) as e:
            return json.dumps({"error": str(e)})
        except GitError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(result)

    @mcp.tool()
    @logged_tool
    async def move_note(note_id: str, folder: str, ctx: Context) -> str:
        """Przenosi notatkę do folderu w aktywnym workspace, tworząc brakującą ścieżkę.
        folder: pełna ścieżka folderu lub pusty string dla root.
        Sukces: {"note_id": "...", "folder": "..."}. Błąd: {"error": "..."}."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        try:
            result = await run_sync(
                note_service.move,
                note_id,
                owner_id=owner_id,
                ws_path=ws_path,
                folder=folder,
            )
        except (ValueError, FileNotFoundError, FileExistsError, GitError) as e:
            return json.dumps({"error": str(e)})
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    @logged_tool
    async def list_folders(ctx: Context) -> str:
        """Zwraca istniejące foldery aktywnego workspace jako JSON array.
        Pusty string oznacza katalog główny workspace."""
        try:
            _, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(await run_sync(note_service.list_folders, ws_path), ensure_ascii=False)

    @mcp.tool()
    @logged_tool
    async def delete_note(note_id: str, ctx: Context) -> str:
        """Usuwa notatkę. Sukces: {"message": "..."}. Błąd: {"error": "..."}."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        try:
            await run_sync(note_service.delete, note_id, owner_id=owner_id, ws_path=ws_path)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps({"message": f"Notatka {note_id} usunięta."})

    @mcp.tool()
    @logged_tool
    async def list_notes(
        ctx: Context,
        tags: list[str] | None = None,
        limit: int = 20,
        folder: str | None = None,
    ) -> str:
        """Zwraca listę notatek jako JSON array. Każda notatka zawiera pole 'folder'.
        folder: opcjonalny filtr — tylko notatki z tego folderu (np. 'Projekty/Klient A').
        Filtr tags używa OR i jest hierarchiczny: podanie 'work' dopasuje też notatki
        otagowane 'work/projects' itd. (dopasowanie po prefiksie segmentów)."""
        try:
            owner_id, ws_name, _ = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        notes = await run_sync(
            note_service.list,
            ws_name,
            owner_id=owner_id,
            tags=tags or None,
            limit=limit,
            folder=folder,
        )
        return json.dumps(notes, ensure_ascii=False)

    @mcp.tool()
    @logged_tool
    async def search_notes(
        query: str,
        ctx: Context,
        workspace: str = "active",
        limit: int = 10,
    ) -> str:
        """Szuka notatek. workspace='active' (domyślnie) lub 'all'.
        Zwraca JSON array — pusty [] gdy brak wyników. Błąd: {"error": "..."}."""
        try:
            owner_id, active_ws, _ = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        real_user_id: str | None = await ctx.get_state("active_user_id")
        ws_param = workspace or "active"
        if ws_param == "all":
            workspaces = await run_sync(workspace_service.list_accessible, real_user_id)
        else:
            workspaces = [ws_param if ws_param != "active" else active_ws]
        results = await run_sync(
            note_service.search, query, workspaces, owner_id=owner_id, limit=limit
        )
        return json.dumps(results, ensure_ascii=False)

    @mcp.tool()
    @logged_tool
    async def reindex_workspace(ctx: Context) -> str:
        """Przebudowuje indeks SQLite z plików .md w aktywnym workspace.
        Sukces: {"message": "...", "count": N}."""
        try:
            owner_id, ws_name, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        result = await run_sync(note_service.reindex, ws_name, owner_id=owner_id, ws_path=ws_path)
        return json.dumps(result)

    @mcp.tool()
    @logged_tool
    async def get_note_history(note_id: str, ctx: Context, limit: int = 50) -> str:
        """Zwraca historię wersji notatki jako JSON array.
        Każdy wpis: {"sha": "...", "message": "...", "timestamp": 1234567890}.
        Błąd: {"error": "..."}."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        try:
            entries = await run_sync(
                note_service.get_history, note_id, owner_id=owner_id, ws_path=ws_path, limit=limit
            )
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(entries)

    @mcp.tool()
    @logged_tool
    async def get_note_at_version(note_id: str, sha: str, ctx: Context) -> str:
        """Zwraca treść notatki z konkretnego commita git.
        sha: pełny lub skrócony hash commita z get_note_history.
        Sukces: {note_id, title, content, tags, ...}. Błąd: {"error": "..."}."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        try:
            version = await run_sync(
                note_service.get_version, note_id, sha, owner_id=owner_id, ws_path=ws_path
            )
        except Exception as e:
            return json.dumps({"error": str(e)})
        return json.dumps(version, ensure_ascii=False)

    @mcp.tool()
    @logged_tool
    async def restore_note_version(note_id: str, sha: str, ctx: Context) -> str:
        """Przywraca notatkę do wersji z podanego commita.
        sha: pełny lub skrócony hash z get_note_history.
        Sukces: {"note_id": "..."}. Błąd: {"error": "..."}."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        try:
            result = await run_sync(
                note_service.restore_version, note_id, sha, owner_id=owner_id, ws_path=ws_path
            )
        except Exception as e:
            return json.dumps({"error": str(e)})
        return json.dumps(result)

    @mcp.tool()
    @logged_tool
    async def add_tag(note_id: str, tags: list[str], ctx: Context) -> str:
        """Dodaje tagi do frontmattera notatki (idempotentnie), bez ruszania treści.
        Zwraca {"note_id","tags","frontmatter_tags","warnings"}. Błąd: {"error": "..."}.
        Uwaga: rusza tylko tagi z frontmattera; inline #hashtagi siedzą w treści."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        try:
            result = await run_sync(note_service.add_tags, note_id, owner_id, ws_path, tags)
        except (GitError, ValueError, FileNotFoundError) as e:
            return json.dumps({"error": str(e)})
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    @logged_tool
    async def remove_tag(note_id: str, tags: list[str], ctx: Context) -> str:
        """Usuwa tagi z frontmattera notatki (idempotentnie), bez ruszania treści.
        Tag obecny tylko jako inline #hashtag nie zniknie — wróci jako warning.
        Zwraca {"note_id","tags","frontmatter_tags","warnings"}. Błąd: {"error": "..."}."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        try:
            result = await run_sync(note_service.remove_tags, note_id, owner_id, ws_path, tags)
        except (GitError, ValueError, FileNotFoundError) as e:
            return json.dumps({"error": str(e)})
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    @logged_tool
    async def set_tags(note_id: str, tags: list[str], ctx: Context, confirm: bool = False) -> str:
        """Nadpisuje frontmatter tagów notatki podaną listą, bez ruszania treści.
        Destrukcyjne: jeśli usunęłoby istniejące tagi, prosi o potwierdzenie (elicitation;
        gdy klient nie wspiera — zwraca requires_confirmation, zawołaj ponownie z confirm=true).
        Zwraca {"note_id","tags","frontmatter_tags","warnings"}. Błąd: {"error": "..."}."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        try:
            result = await run_sync(
                note_service.set_tags, note_id, owner_id, ws_path, tags, confirm
            )
        except (GitError, ValueError, FileNotFoundError) as e:
            return json.dumps({"error": str(e)})

        async def reapply() -> dict:
            return await run_sync(note_service.set_tags, note_id, owner_id, ws_path, tags, True)

        return await _confirm_and_apply(ctx, result, reapply)
