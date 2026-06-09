import json
import subprocess

import pytest
from fastmcp import Client

from kajet_turbo.auth import create_auth
from kajet_turbo.db import Database
from kajet_turbo.mcp import build_mcp
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService


@pytest.fixture
def mcp_server(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    db = Database(str(tmp_path / "test.db"))
    note_repo = NoteRepository(db.engine)
    workspace_repo = WorkspaceRepository(db.engine)
    oauth_repo = OAuthRepository(db.engine)
    provider = create_auth(oauth_repo)
    note_service = NoteService(note_repo)
    workspace_service = WorkspaceService(workspace_repo)
    mcp = build_mcp(note_service, workspace_service, oauth_repo, provider)
    yield mcp, db
    db.close()


@pytest.fixture
def workspaces_dir(tmp_path, monkeypatch, mcp_server):
    ws_dir = tmp_path / "workspaces"
    ws_dir.mkdir()
    ws = ws_dir / "test-ws"
    ws.mkdir()
    subprocess.run(["git", "init", str(ws)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(ws), check=True, capture_output=True)
    monkeypatch.setenv("WORKSPACES_DIR", str(ws_dir))
    return ws_dir


async def test_ping_returns_pong(mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        result = await client.call_tool("ping")
    assert result.content[0].text == "pong"


async def test_list_workspaces(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        result = await client.call_tool("list_workspaces")
    assert "test-ws" in result.content[0].text


async def test_activate_workspace(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        result = await client.call_tool("activate_workspace", {"name": "test-ws"})
    assert "test-ws" in result.content[0].text


async def test_activate_nonexistent_workspace(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        result = await client.call_tool("activate_workspace", {"name": "nie-istnieje"})
    assert "aktywny" not in result.content[0].text.lower()


async def test_save_and_get_note(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool(
            "save_note", {"title": "Moja notatka", "content": "# Treść\n\nTekst.", "tags": ["python"]}
        )
        note_id = json.loads(save_result.content[0].text)["note_id"]
        assert len(note_id) > 0

        get_result = await client.call_tool("get_note", {"note_id": note_id})
        assert "Moja notatka" in get_result.content[0].text


async def test_save_note_creates_file(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool("save_note", {"title": "Plikowa notatka", "content": "treść"})

    ws_path = workspaces_dir / "test-ws"
    files = [p for p in ws_path.rglob("*.md") if ".git" not in str(p)]
    assert len(files) == 1


async def test_delete_note(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool("save_note", {"title": "Do usunięcia", "content": "treść"})
        note_id = json.loads(save_result.content[0].text)["note_id"]
        await client.call_tool("delete_note", {"note_id": note_id})
        get_result = await client.call_tool("get_note", {"note_id": note_id})
        assert "error" in json.loads(get_result.content[0].text)


async def test_update_note(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        save_result = await client.call_tool("save_note", {"title": "Stary tytuł", "content": "stara treść"})
        note_id = json.loads(save_result.content[0].text)["note_id"]
        await client.call_tool("update_note", {"note_id": note_id, "title": "Nowy tytuł", "content": "nowa treść"})
        get_result = await client.call_tool("get_note", {"note_id": note_id})
        assert "Nowy tytuł" in get_result.content[0].text
        assert "nowa treść" in get_result.content[0].text


async def test_list_notes(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool("save_note", {"title": "Notatka 1", "content": "treść 1", "tags": ["python"]})
        await client.call_tool("save_note", {"title": "Notatka 2", "content": "treść 2", "tags": ["js"]})
        result = await client.call_tool("list_notes", {})
        assert "Notatka 1" in result.content[0].text
        assert "Notatka 2" in result.content[0].text


async def test_search_notes_fts_fallback(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool("save_note", {"title": "Python asyncio guide", "content": "Tutorial o coroutines.", "tags": []})
        await client.call_tool("save_note", {"title": "JavaScript intro", "content": "Podstawy JS.", "tags": []})
        result = await client.call_tool("search_notes", {"query": "asyncio"})
        assert "Python asyncio guide" in result.content[0].text
        assert "JavaScript intro" not in result.content[0].text


async def test_search_notes_all_workspaces(workspaces_dir, mcp_server):
    ws2 = workspaces_dir / "drugi-ws"
    ws2.mkdir()
    subprocess.run(["git", "init", str(ws2)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(ws2), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(ws2), check=True, capture_output=True)

    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        await client.call_tool("save_note", {"title": "Notatka w ws1", "content": "Python content.", "tags": []})
        await client.call_tool("activate_workspace", {"name": "drugi-ws"})
        await client.call_tool("save_note", {"title": "Notatka w ws2", "content": "Python content.", "tags": []})
        result = await client.call_tool("search_notes", {"query": "Python", "workspace": "all"})
        text = result.content[0].text
        assert "ws1" in text or "Notatka w ws1" in text
        assert "ws2" in text or "Notatka w ws2" in text


async def test_reindex_workspace(workspaces_dir, mcp_server):
    from kajet_turbo.workspace import note_filepath, write_note_file

    ws_path = workspaces_dir / "test-ws"
    path = note_filepath(str(ws_path), "", "Reindexed note")
    write_note_file(path, "zzz1111", "Reindexed note", ["test"], "2026-06-08T12:00:00+00:00", "2026-06-08T12:00:00+00:00", "treść")

    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await client.call_tool("activate_workspace", {"name": "test-ws"})
        reindex_result = await client.call_tool("reindex_workspace")
        assert "ok" in reindex_result.content[0].text.lower() or "reindeks" in reindex_result.content[0].text.lower()
        search_result = await client.call_tool("search_notes", {"query": "Reindexed"})
        assert "Reindexed note" in search_result.content[0].text


def test_user_repository_create_and_get(tmp_path):
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.users import UserRepository
    db = Database(str(tmp_path / "test.db"))
    repo = UserRepository(db.engine)
    uid = repo.create("a@b.com", "hash123")
    assert len(uid) == 12
    user = repo.get_by_email("a@b.com")
    assert user is not None
    assert user.email == "a@b.com"
    assert user.password_hash == "hash123"
    assert repo.count() == 1
    db.close()


def test_session_repository(tmp_path):
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.users import UserRepository
    from kajet_turbo.repositories.sessions import SessionRepository
    db = Database(str(tmp_path / "test.db"))
    users = UserRepository(db.engine)
    sessions = SessionRepository(db.engine)
    uid = users.create("x@y.com", "h")
    token = sessions.create(uid)
    assert len(token) == 64
    user = sessions.get_user(token)
    assert user is not None
    assert user["email"] == "x@y.com"
    sessions.delete(token)
    assert sessions.get_user(token) is None
    db.close()


def test_workspace_repository(tmp_path):
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.users import UserRepository
    from kajet_turbo.repositories.workspaces import WorkspaceRepository
    db = Database(str(tmp_path / "test.db"))
    users = UserRepository(db.engine)
    workspaces = WorkspaceRepository(db.engine)
    uid = users.create("u@v.com", "h")
    workspaces.grant_access(uid, "ws-alpha")
    assert workspaces.has_access(uid, "ws-alpha")
    assert not workspaces.has_access(uid, "ws-beta")
    assert workspaces.list_user_workspaces(uid) == ["ws-alpha"]
    db.close()


def test_oauth_repository(tmp_path):
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.users import UserRepository
    from kajet_turbo.repositories.oauth import OAuthRepository
    db = Database(str(tmp_path / "test.db"))
    users = UserRepository(db.engine)
    oauth = OAuthRepository(db.engine)
    uid = users.create("o@p.com", "h")

    oauth.upsert_registered_client("cl1", '{"client_id":"cl1"}')
    assert oauth.get_all_registered_clients() == ['{"client_id":"cl1"}']

    oauth.record_client_authorization("cl1", uid)
    assert oauth.get_user_id_by_client("cl1") == uid

    oauth.upsert_access_token("tok1", "cl1", ["read"], None)
    tokens = oauth.get_valid_access_tokens()
    assert any(t["token"] == "tok1" for t in tokens)

    oauth.upsert_refresh_token("ref1", "cl1", ["read"], None)
    rtokens = oauth.get_valid_refresh_tokens()
    assert any(t["token"] == "ref1" for t in rtokens)

    oauth.save_oauth_client("cl2", "secret", ["https://cb.local"], "2026-01-01T00:00:00")
    client = oauth.get_oauth_client("cl2")
    assert client is not None
    assert client["client_secret"] == "secret"
    db.close()


def test_note_repository(tmp_path):
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.notes import NoteRepository
    db = Database(str(tmp_path / "test.db"))
    repo = NoteRepository(db.engine)

    now = "2026-06-08T12:00:00+00:00"
    repo.insert("n001", "ws1", "user-1", "Testowa notatka", ["python"], now, now, "Treść testowa")

    note = repo.get("n001")
    assert note is not None
    assert note.title == "Testowa notatka"
    assert note.owner_id == "user-1"
    assert json.loads(note.tags or "[]") == ["python"]

    notes = repo.list("ws1", owner_id="user-1")
    assert len(notes) == 1

    fts = repo.search_fts("Testowa", "ws1", owner_id="user-1")
    assert len(fts) == 1
    assert fts[0]["note_id"] == "n001"

    repo.update("n001", title="Nowy tytuł", content="Nowa treść", updated_at=now)
    updated = repo.get("n001")
    assert updated.title == "Nowy tytuł"

    repo.delete("n001")
    assert repo.get("n001") is None
    db.close()
