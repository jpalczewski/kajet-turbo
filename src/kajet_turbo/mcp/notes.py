import json

from fastmcp import Context, FastMCP

from kajet_turbo.git_ops import GitError
from kajet_turbo.mcp.workspaces import get_active_workspace
from kajet_turbo.services.notes import NoteService
from kajet_turbo.workspace import list_workspaces as _list_workspaces


def register_notes(mcp: FastMCP, note_service: NoteService) -> None:
    @mcp.tool()
    async def save_note(
        title: str,
        content: str,
        ctx: Context,
        tags: list[str] | None = None,
    ) -> str:
        """Zapisuje nową notatkę. Sukces: {"id": "..."}. Błąd: {"error": "..."}.
        Uwaga: content powinien zawierać rzeczywiste znaki nowej linii (\\n), nie literalne \\\\n."""
        owner_id, ws_name, ws_path = await get_active_workspace(ctx)
        try:
            result = note_service.save(owner_id, ws_name, ws_path, title, content, tags or [])
        except GitError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(result)

    @mcp.tool()
    async def get_note(note_id: str, ctx: Context) -> str:
        """Zwraca notatkę jako JSON object. Błąd: {"error": "..."}."""
        owner_id, _, ws_path = await get_active_workspace(ctx)
        result = note_service.get_with_content(note_id, owner_id=owner_id, ws_path=ws_path)
        if result is None:
            return json.dumps({"error": f"Notatka {note_id} nie znaleziona."})
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    async def update_note(
        note_id: str,
        ctx: Context,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Aktualizuje notatkę. Sukces: {"id": "..."}. Błąd: {"error": "..."}."""
        owner_id, _, ws_path = await get_active_workspace(ctx)
        try:
            result = note_service.update(note_id, owner_id=owner_id, ws_path=ws_path,
                                         title=title, content=content, tags=tags)
        except (ValueError, FileNotFoundError) as e:
            return json.dumps({"error": str(e)})
        except GitError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(result)

    @mcp.tool()
    async def delete_note(note_id: str, ctx: Context) -> str:
        """Usuwa notatkę. Sukces: {"message": "..."}. Błąd: {"error": "..."}."""
        owner_id, _, ws_path = await get_active_workspace(ctx)
        try:
            note_service.delete(note_id, owner_id=owner_id, ws_path=ws_path)
        except ValueError as e:
            return json.dumps({"error": str(e)})
        return json.dumps({"message": f"Notatka {note_id} usunięta."})

    @mcp.tool()
    async def list_notes(
        ctx: Context,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> str:
        """Zwraca listę notatek jako JSON array.
        Filtr tags używa OR — notatka pasuje jeśli ma KTÓRYKOLWIEK z podanych tagów."""
        owner_id, ws_name, _ = await get_active_workspace(ctx)
        notes = note_service.list(ws_name, owner_id=owner_id, tags=tags or None, limit=limit)
        return json.dumps(notes, ensure_ascii=False)

    @mcp.tool()
    async def search_notes(
        query: str,
        ctx: Context,
        workspace: str = "active",
        limit: int = 10,
    ) -> str:
        """Szuka notatek. workspace='active' (domyślnie) lub 'all'.
        Zwraca JSON array — pusty [] gdy brak wyników. Błąd: {"error": "..."}."""
        owner_id, active_ws, _ = await get_active_workspace(ctx)
        real_user_id: str | None = await ctx.get_state("active_user_id")
        ws_param = workspace or "active"
        if ws_param == "all":
            workspaces = _list_workspaces(user_id=real_user_id)
        else:
            workspaces = [ws_param if ws_param != "active" else active_ws]
        results = note_service.search(query, workspaces, owner_id=owner_id, limit=limit)
        return json.dumps(results, ensure_ascii=False)

    @mcp.tool()
    async def reindex_workspace(ctx: Context) -> str:
        """Przebudowuje indeks SQLite z plików .md w aktywnym workspace.
        Sukces: {"message": "...", "count": N}."""
        owner_id, ws_name, ws_path = await get_active_workspace(ctx)
        result = note_service.reindex(ws_name, owner_id=owner_id, ws_path=ws_path)
        return json.dumps(result)
