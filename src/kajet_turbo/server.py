import os

from fastmcp import FastMCP

from kajet_turbo.auth import create_auth


def _build_mcp() -> FastMCP:
    mcp = FastMCP("kajet-turbo", auth=create_auth())

    @mcp.tool()
    def ping() -> str:
        """Health check — zwraca pong."""
        return "pong"

    return mcp


def main() -> None:
    mcp = _build_mcp()
    mcp.run(
        transport="streamable-http",
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8000")),
    )
