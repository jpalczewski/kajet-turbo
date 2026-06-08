import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import FastMCP, Context
from fastmcp.server.lifespan import lifespan
from nanoid import generate

from kajet_turbo.auth import create_auth
from kajet_turbo.git_ops import commit_file, delete_file_commit, GitError
from kajet_turbo.storage import Storage
from kajet_turbo.workspace import (
    list_workspaces as _list_workspaces,
    note_filepath,
    write_note_file,
    read_note_file,
    scan_notes,
)


@lifespan
async def app_lifespan(server):
    storage = Storage()
    try:
        yield {"storage": storage}
    finally:
        storage.close()


def _build_mcp() -> FastMCP:
    mcp = FastMCP("kajet-turbo", auth=create_auth(), lifespan=app_lifespan)

    @mcp.tool()
    def ping() -> str:
        """Health check — zwraca pong."""
        return "pong"

    @mcp.tool()
    async def list_workspaces(ctx: Context) -> list[str]:
        """Zwraca listę dostępnych workspace'ów."""
        return _list_workspaces()

    @mcp.tool()
    async def activate_workspace(name: str, ctx: Context) -> str:
        """Ustawia aktywny workspace dla tej sesji."""
        workspaces = _list_workspaces()
        if name not in workspaces:
            available = ", ".join(workspaces) if workspaces else "(brak)"
            return f"Workspace '{name}' nie istnieje. Dostępne: {available}"
        await ctx.set_state("active_workspace", name)
        return f"Workspace '{name}' aktywny."

    async def _get_workspace(ctx: Context) -> tuple[str, str]:
        """Returns (name, path) of active workspace or raises RuntimeError."""
        name = await ctx.get_state("active_workspace")
        if not name:
            raise RuntimeError("Wywołaj activate_workspace() najpierw.")
        path = str(Path(os.getenv("WORKSPACES_DIR", "/workspaces")) / name)
        return name, path

    @mcp.tool()
    async def save_note(
        title: str,
        content: str,
        ctx: Context,
        tags: list[str] | None = None,
    ) -> str:
        """Zapisuje nową notatkę. Zwraca ID."""
        ws_name, ws_path = await _get_workspace(ctx)
        storage: Storage = ctx.lifespan_context["storage"]

        note_id = generate(size=7)
        now = datetime.now(timezone.utc).isoformat()
        effective_tags = tags or []
        filepath = note_filepath(ws_path, note_id, title)
        relative = str(Path(filepath).relative_to(ws_path))

        write_note_file(filepath, note_id, title, effective_tags, now, now, content)
        try:
            commit_file(ws_path, relative, f"note: add {title}")
        except GitError:
            Path(filepath).unlink(missing_ok=True)
            raise

        storage.insert_note(note_id, ws_name, title, effective_tags, now, now, content)
        return note_id

    @mcp.tool()
    async def get_note(note_id: str, ctx: Context) -> str:
        """Zwraca notatkę jako JSON."""
        _, ws_path = await _get_workspace(ctx)
        storage: Storage = ctx.lifespan_context["storage"]
        meta = storage.get_note(note_id)
        if meta is None:
            return f"Notatka {note_id} nie znaleziono."
        notes_dir = Path(ws_path) / "notes"
        files = list(notes_dir.glob(f"{note_id}-*.md"))
        if not files:
            return f"Plik notatki {note_id} nie znaleziono."
        note_data = read_note_file(str(files[0]))
        return json.dumps({**meta, "content": note_data["content"]}, ensure_ascii=False)

    @mcp.tool()
    async def update_note(
        note_id: str,
        ctx: Context,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Aktualizuje notatkę."""
        ws_name, ws_path = await _get_workspace(ctx)
        storage: Storage = ctx.lifespan_context["storage"]
        meta = storage.get_note(note_id)
        if meta is None:
            return f"Notatka {note_id} nie znaleziona."

        now = datetime.now(timezone.utc).isoformat()
        new_title = title if title is not None else meta["title"]
        new_tags = tags if tags is not None else meta["tags"]
        notes_dir = Path(ws_path) / "notes"
        files = list(notes_dir.glob(f"{note_id}-*.md"))
        if not files:
            return f"Plik notatki {note_id} nie znaleziony."

        note_data = read_note_file(str(files[0]))
        old_content = note_data["content"]
        new_content = content if content is not None else old_content
        write_note_file(str(files[0]), note_id, new_title, new_tags, meta["created_at"], now, new_content)
        try:
            relative = str(files[0].relative_to(ws_path))
            commit_file(ws_path, relative, f"note: update {new_title}")
        except GitError:
            # Restore original file content on git failure
            write_note_file(str(files[0]), note_id, meta["title"], meta["tags"], meta["created_at"], meta["updated_at"], old_content)
            raise
        storage.update_note(note_id, title=new_title, content=new_content, tags=new_tags, updated_at=now)
        return note_id

    @mcp.tool()
    async def delete_note(note_id: str, ctx: Context) -> str:
        """Usuwa notatkę."""
        _, ws_path = await _get_workspace(ctx)
        storage: Storage = ctx.lifespan_context["storage"]
        notes_dir = Path(ws_path) / "notes"
        files = list(notes_dir.glob(f"{note_id}-*.md"))
        if files:
            relative = str(files[0].relative_to(ws_path))
            delete_file_commit(ws_path, relative, f"note: delete {note_id}")
        storage.delete_note(note_id)
        return f"Notatka {note_id} usunięta."

    @mcp.tool()
    async def list_notes(
        ctx: Context,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> str:
        """Zwraca listę notatek jako JSON."""
        ws_name, _ = await _get_workspace(ctx)
        storage: Storage = ctx.lifespan_context["storage"]
        notes = storage.list_notes(ws_name, tags=tags or None, limit=limit)
        return json.dumps(notes, ensure_ascii=False)

    @mcp.tool()
    async def search_notes(
        query: str,
        ctx: Context,
        workspace: str = "active",
        limit: int = 10,
    ) -> str:
        """Szuka notatek. workspace='active' (domyślnie) lub 'all'."""
        storage: Storage = ctx.lifespan_context["storage"]
        ws_param = workspace or "active"

        if ws_param == "all":
            workspaces = _list_workspaces()
            per_ws_limit = limit * 3  # oversample to allow global ranking
        else:
            ws_name = ws_param if ws_param != "active" else await ctx.get_state("active_workspace")
            if not ws_name:
                return "Wywołaj activate_workspace() najpierw."
            workspaces = [ws_name]
            per_ws_limit = limit

        results = []
        for ws in workspaces:
            hits = storage.hybrid_search(query, ws, limit=per_ws_limit)
            results.extend(hits)

        if not results:
            return "Brak wyników."
        return json.dumps(results[:limit], ensure_ascii=False)

    @mcp.tool()
    async def reindex_workspace(ctx: Context) -> str:
        """Przebudowuje indeks SQLite z plików .md w aktywnym workspace."""
        ws_name, ws_path = await _get_workspace(ctx)
        storage: Storage = ctx.lifespan_context["storage"]

        notes = scan_notes(ws_path)
        storage.delete_workspace_notes(ws_name)

        for note in notes:
            if not note["id"]:
                continue
            storage.insert_note(
                note["id"],
                ws_name,
                note["title"] or "",
                note["tags"] or [],
                str(note["created_at"] or ""),
                str(note["updated_at"] or ""),
                note["content"] or "",
            )

        return f"Reindeksowano {len(notes)} notatek w workspace '{ws_name}'."

    return mcp


def main() -> None:
    mcp = _build_mcp()
    mcp.run(
        transport="streamable-http",
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
    )
