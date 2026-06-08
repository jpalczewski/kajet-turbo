import json
import os
from datetime import UTC, datetime
from pathlib import Path

from fastmcp import Context, FastMCP
from fastmcp.server.lifespan import lifespan
from nanoid import generate
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from kajet_turbo.auth import create_auth
from kajet_turbo.git_ops import GitError, commit_file, delete_file_commit
from kajet_turbo.storage import Storage
from kajet_turbo.workspace import (
    create_workspace as _create_workspace,
)
from kajet_turbo.workspace import (
    list_workspaces as _list_workspaces,
)
from kajet_turbo.workspace import (
    note_filepath,
    read_note_file,
    scan_notes,
    write_note_file,
)


@lifespan
async def app_lifespan(server):
    storage = Storage()
    try:
        yield {"storage": storage}
    finally:
        storage.close()


def _build_mcp() -> FastMCP:
    from kajet_turbo.auth import hash_password, verify_password

    _auth_storage = Storage()

    # Seed admin user on first run
    admin_email = os.getenv("KAJET_ADMIN_EMAIL")
    admin_password = os.getenv("KAJET_ADMIN_PASSWORD")
    if admin_email and admin_password and _auth_storage.user_count() == 0:
        _auth_storage.create_user(admin_email, hash_password(admin_password))

    provider = create_auth(_auth_storage)
    mcp = FastMCP("kajet-turbo", auth=provider, lifespan=app_lifespan)

    @mcp.tool()
    def ping() -> str:
        """Health check."""
        return "pong"

    @mcp.tool()
    async def list_workspaces(ctx: Context) -> str:
        """Zwraca listę dostępnych workspace'ów. Odpowiedź: JSON array stringów."""
        return json.dumps(_list_workspaces())

    @mcp.tool()
    async def activate_workspace(name: str, ctx: Context) -> str:
        """Ustawia aktywny workspace dla tej sesji.
        Sukces: {"message": "..."}. Błąd: {"error": "...", "available": [...]}."""
        workspaces = _list_workspaces()
        if name not in workspaces:
            return json.dumps({"error": f"Workspace '{name}' nie istnieje.", "available": workspaces})
        await ctx.set_state("active_workspace", name)
        return json.dumps({"message": f"Workspace '{name}' aktywny."})

    @mcp.tool()
    async def create_workspace(name: str, ctx: Context) -> str:
        """Tworzy nowy workspace z repozytorium git.
        Sukces: {"message": "..."}. Błąd: {"error": "..."}."""
        try:
            _create_workspace(name)
        except (ValueError, FileExistsError) as e:
            return json.dumps({"error": str(e)})
        return json.dumps({"message": f"Workspace '{name}' utworzony."})

    async def _get_workspace(ctx: Context) -> tuple[str, str]:
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
        """Zapisuje nową notatkę. Sukces: {"id": "..."}. Błąd: {"error": "..."}.
        Uwaga: content powinien zawierać rzeczywiste znaki nowej linii (\\n), nie literalne \\\\n."""
        ws_name, ws_path = await _get_workspace(ctx)
        storage: Storage = ctx.lifespan_context["storage"]

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

        storage.insert_note(note_id, ws_name, title, effective_tags, now, now, content)
        return json.dumps({"id": note_id})

    @mcp.tool()
    async def get_note(note_id: str, ctx: Context) -> str:
        """Zwraca notatkę jako JSON object. Błąd: {"error": "..."}."""
        _, ws_path = await _get_workspace(ctx)
        storage: Storage = ctx.lifespan_context["storage"]
        meta = storage.get_note(note_id)
        if meta is None:
            return json.dumps({"error": f"Notatka {note_id} nie znaleziona."})
        notes_dir = Path(ws_path) / "notes"
        files = list(notes_dir.glob(f"{note_id}-*.md"))
        if not files:
            return json.dumps({"error": f"Plik notatki {note_id} nie znaleziony."})
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
        """Aktualizuje notatkę. Sukces: {"id": "..."}. Błąd: {"error": "..."}."""
        ws_name, ws_path = await _get_workspace(ctx)
        storage: Storage = ctx.lifespan_context["storage"]
        meta = storage.get_note(note_id)
        if meta is None:
            return json.dumps({"error": f"Notatka {note_id} nie znaleziona."})

        now = datetime.now(UTC).isoformat()
        new_title = title if title is not None else meta["title"]
        new_tags = tags if tags is not None else meta["tags"]
        notes_dir = Path(ws_path) / "notes"
        files = list(notes_dir.glob(f"{note_id}-*.md"))
        if not files:
            return json.dumps({"error": f"Plik notatki {note_id} nie znaleziony."})

        note_data = read_note_file(str(files[0]))
        old_content = note_data["content"]
        new_content = content if content is not None else old_content
        write_note_file(str(files[0]), note_id, new_title, new_tags, meta["created_at"], now, new_content)
        try:
            relative = str(files[0].relative_to(ws_path))
            commit_file(ws_path, relative, f"note: update {new_title}")
        except GitError:
            write_note_file(str(files[0]), note_id, meta["title"], meta["tags"], meta["created_at"], meta["updated_at"], old_content)
            raise
        storage.update_note(note_id, title=new_title, content=new_content, tags=new_tags, updated_at=now)
        return json.dumps({"id": note_id})

    @mcp.tool()
    async def delete_note(note_id: str, ctx: Context) -> str:
        """Usuwa notatkę. Sukces: {"message": "..."}."""
        _, ws_path = await _get_workspace(ctx)
        storage: Storage = ctx.lifespan_context["storage"]
        notes_dir = Path(ws_path) / "notes"
        files = list(notes_dir.glob(f"{note_id}-*.md"))
        if files:
            relative = str(files[0].relative_to(ws_path))
            delete_file_commit(ws_path, relative, f"note: delete {note_id}")
        storage.delete_note(note_id)
        return json.dumps({"message": f"Notatka {note_id} usunięta."})

    @mcp.tool()
    async def list_notes(
        ctx: Context,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> str:
        """Zwraca listę notatek jako JSON array.
        Filtr tags używa OR — notatka pasuje jeśli ma KTÓRYKOLWIEK z podanych tagów."""
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
        """Szuka notatek. workspace='active' (domyślnie) lub 'all'.
        Zwraca JSON array — pusty [] gdy brak wyników. Błąd: {"error": "..."}."""
        storage: Storage = ctx.lifespan_context["storage"]
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
            hits = storage.hybrid_search(query, ws, limit=per_ws_limit)
            results.extend(hits)

        return json.dumps(results[:limit], ensure_ascii=False)

    @mcp.tool()
    async def reindex_workspace(ctx: Context) -> str:
        """Przebudowuje indeks SQLite z plików .md w aktywnym workspace.
        Sukces: {"message": "...", "count": N}."""
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

        return json.dumps({"message": f"Reindeksowano {len(notes)} notatek w workspace '{ws_name}'.", "count": len(notes)})

    @mcp.custom_route("/api/login", methods=["POST"])
    async def api_login(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        email = str(body.get("email", ""))
        password = str(body.get("password", ""))
        pending_id = str(body.get("pending_id", ""))

        if not pending_id or pending_id not in provider._pending:
            return JSONResponse({"error": "Wygasły lub nieprawidłowy pending_id."}, status_code=400)

        user = _auth_storage.get_user_by_email(email)
        if not user or not verify_password(user["password_hash"] or "", password):
            return JSONResponse({"error": "Nieprawidłowy email lub hasło."}, status_code=401)

        try:
            redirect_uri = await provider.complete_authorization(pending_id)
        except ValueError:
            return JSONResponse({"error": "Wygasły pending_id."}, status_code=400)

        return JSONResponse({"redirect_uri": redirect_uri})

    return mcp


class _SPAFiles:
    """Starlette mount that serves index.html for any path without a matching file (SPA fallback)."""

    def __init__(self, directory: str) -> None:
        from starlette.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException as StarletteHTTPException

        class _SPA(StaticFiles):
            async def get_response(self, path: str, scope):
                try:
                    return await super().get_response(path, scope)
                except StarletteHTTPException as exc:
                    if exc.status_code == 404:
                        return await super().get_response("index.html", scope)
                    raise

        self._app = _SPA(directory=directory, html=True)

    async def __call__(self, scope, receive, send) -> None:
        await self._app(scope, receive, send)


def main() -> None:
    mcp = _build_mcp()
    app = mcp.http_app(transport="streamable-http")

    dist = Path(__file__).parent.parent.parent / "dist"
    if dist.exists():
        app.mount("/", _SPAFiles(str(dist)))

    import uvicorn
    uvicorn.run(app, host=os.getenv("MCP_HOST", "0.0.0.0"), port=int(os.getenv("MCP_PORT", "8000")))
