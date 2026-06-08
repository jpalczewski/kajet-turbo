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


async def test_save_and_get_note(workspaces_dir):
    from kajet_turbo.server import _build_mcp
    mcp = _build_mcp()
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool(
            "save_note", {"title": "Moja notatka", "content": "# Treść\n\nTekst.", "tags": ["python"]}
        )
        note_id = save_result.content[0].text.strip()
        assert len(note_id) > 0

        get_result = await client.call_tool("get_note", {"note_id": note_id})
        assert "Moja notatka" in get_result.content[0].text


async def test_save_note_creates_file(workspaces_dir):
    from kajet_turbo.server import _build_mcp
    mcp = _build_mcp()
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool("save_note", {"title": "Plikowa notatka", "content": "treść"})

    ws_path = workspaces_dir / "test-ws" / "notes"
    files = list(ws_path.glob("*.md"))
    assert len(files) == 1


async def test_delete_note(workspaces_dir):
    from kajet_turbo.server import _build_mcp
    mcp = _build_mcp()
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool("save_note", {"title": "Do usunięcia", "content": "treść"})
        note_id = save_result.content[0].text.strip()
        await client.call_tool("delete_note", {"note_id": note_id})
        get_result = await client.call_tool("get_note", {"note_id": note_id})
        assert "nie znaleziono" in get_result.content[0].text.lower()


async def test_update_note(workspaces_dir):
    from kajet_turbo.server import _build_mcp
    mcp = _build_mcp()
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool("save_note", {"title": "Stary tytuł", "content": "stara treść"})
        note_id = save_result.content[0].text.strip()
        await client.call_tool("update_note", {"note_id": note_id, "title": "Nowy tytuł", "content": "nowa treść"})
        get_result = await client.call_tool("get_note", {"note_id": note_id})
        assert "Nowy tytuł" in get_result.content[0].text
        assert "nowa treść" in get_result.content[0].text


async def test_list_notes(workspaces_dir):
    from kajet_turbo.server import _build_mcp
    mcp = _build_mcp()
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool("save_note", {"title": "Notatka 1", "content": "treść 1", "tags": ["python"]})
        await client.call_tool("save_note", {"title": "Notatka 2", "content": "treść 2", "tags": ["js"]})
        result = await client.call_tool("list_notes", {})
        assert "Notatka 1" in result.content[0].text
        assert "Notatka 2" in result.content[0].text
