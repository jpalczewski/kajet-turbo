from fastmcp import Client


async def test_ping_returns_pong(monkeypatch):
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    from kajet_turbo.server import _build_mcp

    mcp = _build_mcp()
    async with Client(mcp) as client:
        result = await client.call_tool("ping")

    assert result.content[0].text == "pong"
