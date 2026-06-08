import os
from fastmcp import FastMCP, Context
from fastmcp.server.lifespan import lifespan
from pathlib import Path

from kajet_turbo.auth import create_auth
from kajet_turbo.storage import Storage
from kajet_turbo.workspace import list_workspaces as _list_workspaces


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

    return mcp


def main() -> None:
    mcp = _build_mcp()
    mcp.run(
        transport="streamable-http",
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
    )
