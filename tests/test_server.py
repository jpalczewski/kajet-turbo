from fastmcp import Client


async def test_ping_returns_pong(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    from kajet_turbo.server import _build_mcp

    mcp = _build_mcp()
    async with Client(mcp) as client:
        result = await client.call_tool("ping")

    assert result.content[0].text == "pong"


import os
import subprocess
import pytest
from pathlib import Path


@pytest.fixture
def workspaces_dir(tmp_path, monkeypatch):
    ws_dir = tmp_path / "workspaces"
    ws_dir.mkdir()
    ws = ws_dir / "test-ws"
    ws.mkdir()
    (ws / "notes").mkdir()
    subprocess.run(["git", "init", str(ws)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(ws), check=True, capture_output=True)
    monkeypatch.setenv("WORKSPACES_DIR", str(ws_dir))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    return ws_dir


async def test_list_workspaces(workspaces_dir):
    from kajet_turbo.server import _build_mcp
    mcp = _build_mcp()
    async with Client(mcp) as client:
        result = await client.call_tool("list_workspaces")
    assert "test-ws" in result.content[0].text


async def test_activate_workspace(workspaces_dir):
    from kajet_turbo.server import _build_mcp
    mcp = _build_mcp()
    async with Client(mcp) as client:
        result = await client.call_tool("activate_workspace", {"name": "test-ws"})
    assert "test-ws" in result.content[0].text


async def test_activate_nonexistent_workspace(workspaces_dir):
    from kajet_turbo.server import _build_mcp
    mcp = _build_mcp()
    async with Client(mcp) as client:
        result = await client.call_tool("activate_workspace", {"name": "nie-istnieje"})
    # Should NOT say workspace is active — the nonexistent workspace should be rejected
    assert "aktywny" not in result.content[0].text.lower()
