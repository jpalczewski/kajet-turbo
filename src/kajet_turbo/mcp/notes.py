import json
from datetime import UTC, datetime
from pathlib import Path

from fastmcp import Context, FastMCP
from nanoid import generate

from kajet_turbo.git_ops import GitError, commit_file, delete_file_commit
from kajet_turbo.mcp.workspaces import get_active_workspace
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.workspace import (
    list_workspaces as _list_workspaces,
    note_filepath,
    read_note_file,
    scan_notes,
    write_note_file,
)


def register_notes(mcp: FastMCP, note_repo: NoteRepository) -> None:
    @mcp.tool()
    async def save_note(
        title: str,
        content: str,
        ctx: Context,
        tags: list[str] | None = None,
    ) -> str:
        """Zapisuje nową notatkę. Sukces: {"id": "..."}. Błąd: {"error": "..."}.
        Uwaga: content powinien zawierać rzeczywiste znaki nowej linii (\\n), nie literalne \\\\n."""
        ws_name, ws_path = await get_active_workspace(ctx)
        note_id = generate(size=7)
        now = datetime.now(UTC).isoformat()
        effective_tags = tags or []
        filepath = note_filepath(ws_path, note_id, title)
        relative = str(Path(filepath).relative_to(ws_path))
        write_note_file(filepath, note_id, title, effective_tags, now, now, content)
        try:
            commit_file(ws_path, relative, f"note: add {title}")
        except GitError:
            Path(filepath).unlink(missing_ok=True)
            raise
        note_repo.insert(note_id, ws_name, title, effective_tags, now, now, content)
        return json.dumps({"id": note_id})

    @mcp.tool()
    async def get_note(note_id: str, ctx: Context) -> str:
        """Zwraca notatkę jako JSON object. Błąd: {"error": "..."}."""
        _, ws_path = await get_active_workspace(ctx)
        note = note_repo.get(note_id)
        if note is None:
            return json.dumps({"error": f"Notatka {note_id} nie znaleziona."})
        notes_dir = Path(ws_path) / "notes"
        files = list(notes_dir.glob(f"{note_id}-*.md"))
        if not files:
            return json.dumps({"error": f"Plik notatki {note_id} nie znaleziony."})
        note_data = read_note_file(str(files[0]))
        return json.dumps({
            "id": note.id, "workspace": note.workspace, "title": note.title,
            "tags": json.loads(note.tags or "[]"), "created_at": note.created_at,
            "updated_at": note.updated_at, "content": note_data["content"],
        }, ensure_ascii=False)

    @mcp.tool()
    async def update_note(
        note_id: str,
        ctx: Context,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Aktualizuje notatkę. Sukces: {"id": "..."}. Błąd: {"error": "..."}."""
        ws_name, ws_path = await get_active_workspace(ctx)
        note = note_repo.get(note_id)
        if note is None:
            return json.dumps({"error": f"Notatka {note_id} nie znaleziona."})
        now = datetime.now(UTC).isoformat()
        new_title = title if title is not None else note.title
        current_tags = json.loads(note.tags or "[]")
        new_tags = tags if tags is not None else current_tags
        notes_dir = Path(ws_path) / "notes"
        files = list(notes_dir.glob(f"{note_id}-*.md"))
        if not files:
            return json.dumps({"error": f"Plik notatki {note_id} nie znaleziony."})
        note_data = read_note_file(str(files[0]))
        old_content = note_data["content"]
        new_content = content if content is not None else old_content
        write_note_file(str(files[0]), note_id, new_title, new_tags, note.created_at, now, new_content)
        try:
            relative = str(files[0].relative_to(ws_path))
            commit_file(ws_path, relative, f"note: update {new_title}")
        except GitError:
            write_note_file(str(files[0]), note_id, note.title, current_tags, note.created_at, note.updated_at, old_content)
            raise
        note_repo.update(note_id, title=new_title, content=new_content, tags=new_tags, updated_at=now)
        return json.dumps({"id": note_id})

    @mcp.tool()
    async def delete_note(note_id: str, ctx: Context) -> str:
        """Usuwa notatkę. Sukces: {"message": "..."}."""
        _, ws_path = await get_active_workspace(ctx)
        notes_dir = Path(ws_path) / "notes"
        files = list(notes_dir.glob(f"{note_id}-*.md"))
        if files:
            relative = str(files[0].relative_to(ws_path))
            delete_file_commit(ws_path, relative, f"note: delete {note_id}")
        note_repo.delete(note_id)
        return json.dumps({"message": f"Notatka {note_id} usunięta."})

    @mcp.tool()
    async def list_notes(
        ctx: Context,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> str:
        """Zwraca listę notatek jako JSON array.
        Filtr tags używa OR — notatka pasuje jeśli ma KTÓRYKOLWIEK z podanych tagów."""
        ws_name, _ = await get_active_workspace(ctx)
        notes = note_repo.list(ws_name, tags=tags or None, limit=limit)
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
        ws_param = workspace or "active"
        if ws_param == "all":
            workspaces = _list_workspaces()
            per_ws_limit = limit * 3
        else:
            ws_name = ws_param if ws_param != "active" else await ctx.get_state("active_workspace")
            if not ws_name:
                return json.dumps({"error": "Wywołaj activate_workspace() najpierw."})
            workspaces = [ws_name]
            per_ws_limit = limit
        results = []
        for ws in workspaces:
            hits = note_repo.hybrid_search(query, ws, limit=per_ws_limit)
            results.extend(hits)
        return json.dumps(results[:limit], ensure_ascii=False)

    @mcp.tool()
    async def reindex_workspace(ctx: Context) -> str:
        """Przebudowuje indeks SQLite z plików .md w aktywnym workspace.
        Sukces: {"message": "...", "count": N}."""
        ws_name, ws_path = await get_active_workspace(ctx)
        notes = scan_notes(ws_path)
        note_repo.delete_workspace_notes(ws_name)
        for note in notes:
            if not note["id"]:
                continue
            note_repo.insert(
                note["id"],
                ws_name,
                note["title"] or "",
                note["tags"] or [],
                str(note["created_at"] or ""),
                str(note["updated_at"] or ""),
                note["content"] or "",
            )
        return json.dumps({"message": f"Reindeksowano {len(notes)} notatek w workspace '{ws_name}'.", "count": len(notes)})
