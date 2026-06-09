import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlmodel import Session
from starlette.testclient import TestClient

from kajet_turbo.api.workspaces import router
from kajet_turbo.db import Database
from kajet_turbo.dependencies import get_note_service, get_workspace_service
from kajet_turbo.models import User
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService


def _create_user(engine, user_id: str = "u1") -> None:
    with Session(engine) as session:
        session.add(User(id=user_id, email=f"{user_id}@test.com", password_hash="x", created_at="2026-01-01"))
        session.commit()


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspaces" / "u1" / "test-ws"
    ws.mkdir(parents=True)
    subprocess.run(["git", "init", str(ws)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(ws), check=True, capture_output=True)
    return ws


@pytest.fixture
def auth_client(tmp_path, workspace, monkeypatch):
    monkeypatch.setenv("WORKSPACES_DIR", str(workspace.parent.parent))
    db = Database(str(tmp_path / "test.db"))
    note_repo = NoteRepository(db.engine)
    ws_repo = WorkspaceRepository(db.engine)
    note_svc = NoteService(note_repo)
    ws_svc = WorkspaceService(ws_repo)
    _create_user(db.engine)
    ws_repo.grant_access("u1", "test-ws")

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_note_service] = lambda: note_svc
    app.dependency_overrides[get_workspace_service] = lambda: ws_svc
    monkeypatch.setattr("kajet_turbo.api.workspaces.get_session_user", lambda req: {"id": "u1"})

    with TestClient(app) as client:
        yield client, note_svc, str(workspace)
    db.close()


@pytest.fixture
def no_access_client(tmp_path, workspace, monkeypatch):
    monkeypatch.setenv("WORKSPACES_DIR", str(workspace.parent.parent))
    db = Database(str(tmp_path / "test.db"))
    note_repo = NoteRepository(db.engine)
    ws_repo = WorkspaceRepository(db.engine)
    note_svc = NoteService(note_repo)
    ws_svc = WorkspaceService(ws_repo)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_note_service] = lambda: note_svc
    app.dependency_overrides[get_workspace_service] = lambda: ws_svc
    monkeypatch.setattr("kajet_turbo.api.workspaces.get_session_user", lambda req: {"id": "u1"})

    with TestClient(app) as client:
        yield client
    db.close()


@pytest.fixture
def anon_client(tmp_path, workspace, monkeypatch):
    monkeypatch.setenv("WORKSPACES_DIR", str(workspace.parent.parent))
    db = Database(str(tmp_path / "test.db"))
    note_repo = NoteRepository(db.engine)
    ws_repo = WorkspaceRepository(db.engine)
    note_svc = NoteService(note_repo)
    ws_svc = WorkspaceService(ws_repo)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_note_service] = lambda: note_svc
    app.dependency_overrides[get_workspace_service] = lambda: ws_svc
    monkeypatch.setattr("kajet_turbo.api.workspaces.get_session_user", lambda req: None)

    with TestClient(app) as client:
        yield client
    db.close()


def test_html_returns_rendered_content(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Testowa notatka", "# Nagłówek\n\nAkapit.", ["python"])["note_id"]

    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/html")

    assert resp.status_code == 200
    data = resp.json()
    assert data["note_id"] == note_id
    assert data["title"] == "Testowa notatka"
    assert data["tags"] == ["python"]
    assert "<h1>" in data["content_html"]
    assert "Nagłówek" in data["content_html"]
    assert "Akapit" in data["content_html"]
    assert "content" not in data


def test_html_returns_401_when_not_logged_in(anon_client):
    resp = anon_client.get("/api/workspaces/test-ws/notes/abc1234/html")
    assert resp.status_code == 401


def test_html_returns_403_when_no_access(no_access_client):
    resp = no_access_client.get("/api/workspaces/test-ws/notes/abc1234/html")
    assert resp.status_code == 403


def test_html_returns_404_when_note_missing(auth_client):
    client, _, _ = auth_client
    resp = client.get("/api/workspaces/test-ws/notes/nonexistent/html")
    assert resp.status_code == 404


def test_markdown_returns_raw_content(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "MD notatka", "# Hello\n\nŚwiat.", [])["note_id"]

    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/markdown")

    assert resp.status_code == 200
    data = resp.json()
    assert data["note_id"] == note_id
    assert data["title"] == "MD notatka"
    assert data["content"] == "# Hello\n\nŚwiat."
    assert "content_html" not in data


def test_markdown_returns_401_when_not_logged_in(anon_client):
    resp = anon_client.get("/api/workspaces/test-ws/notes/abc1234/markdown")
    assert resp.status_code == 401


def test_markdown_returns_403_when_no_access(no_access_client):
    resp = no_access_client.get("/api/workspaces/test-ws/notes/abc1234/markdown")
    assert resp.status_code == 403


def test_markdown_returns_404_when_note_missing(auth_client):
    client, _, _ = auth_client
    resp = client.get("/api/workspaces/test-ws/notes/nonexistent/markdown")
    assert resp.status_code == 404


def test_html_strips_script_tags(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save(
        "u1", "test-ws", ws_path, "XSS test",
        '<script>alert(1)</script>\n\n## Bezpieczny nagłówek', []
    )["note_id"]

    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/html")

    assert resp.status_code == 200
    html = resp.json()["content_html"]
    assert "<script>" not in html
    assert "</script>" not in html
    assert "Bezpieczny" in html


def test_html_strips_javascript_urls(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save(
        "u1", "test-ws", ws_path, "JS URL test",
        '[kliknij](javascript:alert(1))', []
    )["note_id"]

    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/html")

    assert resp.status_code == 200
    html = resp.json()["content_html"]
    assert "javascript:" not in html


def test_save_note_creates_file_in_folder(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "Moja notatka", "content", [], folder="Projekty/Klient A")
    expected = Path(ws_path) / "Projekty" / "Klient A" / "Moja notatka.md"
    assert expected.exists()


def test_save_note_root_no_notes_subdir(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "Root notatka", "content", [])
    expected = Path(ws_path) / "Root notatka.md"
    assert expected.exists()
    assert not (Path(ws_path) / "notes").exists()


def test_save_note_duplicate_title_same_folder_raises(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "Dup", "content", [], folder="F")
    with pytest.raises(ValueError, match="już istnieje"):
        note_svc.save("u1", "test-ws", ws_path, "Dup", "content2", [], folder="F")


def test_update_note_title_renames_file(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Stary tytuł", "content", [])["note_id"]
    old_path = Path(ws_path) / "Stary tytuł.md"
    assert old_path.exists()

    note_svc.update(note_id, owner_id="u1", ws_path=ws_path, title="Nowy tytuł")
    assert not old_path.exists()
    assert (Path(ws_path) / "Nowy tytuł.md").exists()


def test_update_note_folder_moves_file(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Przenoszona", "content", [])["note_id"]

    note_svc.update(note_id, owner_id="u1", ws_path=ws_path, folder="Archiwum")
    assert not (Path(ws_path) / "Przenoszona.md").exists()
    assert (Path(ws_path) / "Archiwum" / "Przenoszona.md").exists()


def test_get_with_content_uses_db_path(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Notatka get", "treść", [], folder="Docs")["note_id"]
    result = note_svc.get_with_content(note_id, owner_id="u1", ws_path=ws_path)
    assert result is not None
    assert result["folder"] == "Docs"
    assert result["content"] == "treść"


def test_delete_uses_db_path(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Do usunięcia", "content", [], folder="Trash")["note_id"]
    filepath = Path(ws_path) / "Trash" / "Do usunięcia.md"
    assert filepath.exists()

    note_svc.delete(note_id, owner_id="u1", ws_path=ws_path)
    assert not filepath.exists()


def test_reindex_finds_notes_in_subfolders(auth_client):
    client, note_svc, ws_path = auth_client
    from kajet_turbo.workspace import note_filepath, write_note_file
    p1 = note_filepath(ws_path, "", "Root note")
    write_note_file(p1, "rid1", "Root note", [], "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "r")
    p2 = note_filepath(ws_path, "Sub", "Sub note")
    write_note_file(p2, "sid1", "Sub note", [], "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "s")

    result = note_svc.reindex("test-ws", owner_id="u1", ws_path=ws_path)
    assert result["count"] == 2


def test_list_notes_returns_folder(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "Lista notatka", "content", [], folder="Projekty")
    resp = client.get("/api/workspaces/test-ws/notes")
    assert resp.status_code == 200
    notes = resp.json()["notes"]
    assert len(notes) == 1
    assert notes[0]["folder"] == "Projekty"


def test_list_notes_folder_filter(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "W folderze", "content", [], folder="A")
    note_svc.save("u1", "test-ws", ws_path, "W rootu", "content", [])
    resp = client.get("/api/workspaces/test-ws/notes?folder=A")
    assert resp.status_code == 200
    notes = resp.json()["notes"]
    assert len(notes) == 1
    assert notes[0]["title"] == "W folderze"


def test_html_returns_folder(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "HTML folder", "treść", [], folder="Docs")["note_id"]
    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/html")
    assert resp.status_code == 200
    assert resp.json()["folder"] == "Docs"


def test_markdown_returns_folder(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "MD folder", "treść", [], folder="Arch")["note_id"]
    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/markdown")
    assert resp.status_code == 200
    assert resp.json()["folder"] == "Arch"
