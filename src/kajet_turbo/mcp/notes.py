import json

from fastmcp import Context, FastMCP

from kajet_turbo.git_ops import GitError
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.workspaces import get_active_workspace
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def register_notes(mcp: FastMCP, note_service: NoteService, workspace_service: WorkspaceService) -> None:
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
        Uwaga: content powinien zawierać rzeczywiste znaki nowej linii (\\n), nie literalne \\\\n."""
        try:
            owner_id, ws_name, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        try:
            result = note_service.save(owner_id, ws_name, ws_path, title, content, tags or [], folder=folder)
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
        result = note_service.get_with_content(note_id, owner_id=owner_id, ws_path=ws_path)
        if result is None:
            return json.dumps({"error": f"Notatka {note_id} nie znaleziona."})
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    @logged_tool
    async def update_note(
        note_id: str,
        ctx: Context,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        folder: str | None = None,
    ) -> str:
        """Aktualizuje notatkę. folder opcjonalny — jeśli podany, przenosi notatkę do nowego folderu.
        Sukces: {"note_id": "..."}. Błąd: {"error": "..."}."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        try:
            result = note_service.update(note_id, owner_id=owner_id, ws_path=ws_path,
                                         title=title, content=content, tags=tags, folder=folder)
        except (ValueError, FileNotFoundError) as e:
            return json.dumps({"error": str(e)})
        except GitError as e:
            return json.dumps({"error": str(e)})
        return json.dumps(result)

    @mcp.tool()
    @logged_tool
    async def delete_note(note_id: str, ctx: Context) -> str:
        """Usuwa notatkę. Sukces: {"message": "..."}. Błąd: {"error": "..."}."""
        try:
            owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        try:
            note_service.delete(note_id, owner_id=owner_id, ws_path=ws_path)
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
        Filtr tags używa OR — notatka pasuje jeśli ma KTÓRYKOLWIEK z podanych tagów."""
        try:
            owner_id, ws_name, _ = await get_active_workspace(ctx, workspace_service)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})
        notes = note_service.list(ws_name, owner_id=owner_id, tags=tags or None, limit=limit, folder=folder)
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
            workspaces = workspace_service.list_accessible(real_user_id)
        else:
            workspaces = [ws_param if ws_param != "active" else active_ws]
        results = note_service.search(query, workspaces, owner_id=owner_id, limit=limit)
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
        result = note_service.reindex(ws_name, owner_id=owner_id, ws_path=ws_path)
        return json.dumps(result)
