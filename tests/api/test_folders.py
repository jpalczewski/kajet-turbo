from pathlib import Path

import pytest


def test_ls_root_returns_folders_and_entries(auth_client):
    client, note_service, workspace = auth_client
    note_service.save("u1", "test-ws", workspace, "Root Note", "content", [])
    note_service.save("u1", "test-ws", workspace, "Deep Note", "content", [], folder="docs")

    response = client.get("/api/workspaces/test-ws/ls")

    assert response.status_code == 200
    data = response.json()
    assert "docs" in data["folders"]
    assert data["entries"][0]["title"] == "Root Note"
    assert {"size_bytes", "note_id"} <= data["entries"][0].keys()


def test_ls_subfolder_returns_notes_in_folder(auth_client):
    client, note_service, workspace = auth_client
    note_service.save("u1", "test-ws", workspace, "Doc", "content", [], folder="docs")
    note_service.save("u1", "test-ws", workspace, "Root", "content", [])

    response = client.get("/api/workspaces/test-ws/ls?path=docs")

    assert response.status_code == 200
    assert [entry["title"] for entry in response.json()["entries"]] == ["Doc"]


def test_ls_nonexistent_path_returns_404(auth_client):
    response = auth_client.get("/api/workspaces/test-ws/ls?path=missing")

    assert response.status_code == 404


def test_ls_recursive_returns_all_expanded_folders(auth_client):
    client, note_service, workspace = auth_client
    note_service.save("u1", "test-ws", workspace, "Guide", "content", [], folder="docs/guide")
    note_service.save("u1", "test-ws", workspace, "Note", "content", [], folder="notes")

    response = client.get("/api/workspaces/test-ws/ls?recursive=true")

    assert response.status_code == 200
    assert set(response.json()["folders"]) >= {"docs", "docs/guide", "notes"}
    assert response.json()["entries"] == []


@pytest.mark.parametrize(
    ("client_fixture", "expected_status"),
    [("anon_client", 401), ("no_access_client", 403)],
)
def test_ls_requires_authorized_workspace(request, client_fixture, expected_status):
    client = request.getfixturevalue(client_fixture)

    assert client.get("/api/workspaces/test-ws/ls").status_code == expected_status


@pytest.mark.parametrize("path", ["docs", "a/b/c"])
def test_create_folder(auth_client, path):
    client, _, workspace = auth_client

    response = client.post("/api/workspaces/test-ws/folders", json={"path": path})

    assert response.status_code == 200
    assert response.json() == {"path": path}
    assert (Path(workspace) / path / ".gitkeep").exists()


def test_create_folder_is_idempotent(auth_client):
    client, _, _ = auth_client
    client.post("/api/workspaces/test-ws/folders", json={"path": "docs"})

    response = client.post("/api/workspaces/test-ws/folders", json={"path": "docs"})

    assert response.status_code == 200
    assert response.json() == {"path": "docs"}


@pytest.mark.parametrize("path", ["../evil", "a//b", "my folder?", "."])
def test_create_folder_rejects_invalid_path(auth_client, path):
    response = auth_client.post("/api/workspaces/test-ws/folders", json={"path": path})

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("client_fixture", "expected_status"),
    [("anon_client", 401), ("no_access_client", 403)],
)
def test_create_folder_requires_authorized_workspace(request, client_fixture, expected_status):
    client = request.getfixturevalue(client_fixture)

    response = client.post("/api/workspaces/test-ws/folders", json={"path": "docs"})

    assert response.status_code == expected_status


def test_ls_recursive_includes_empty_folder(auth_client):
    client, _, workspace = auth_client
    gitkeep = Path(workspace) / "empty-dir" / ".gitkeep"
    gitkeep.parent.mkdir(parents=True)
    gitkeep.touch()

    response = client.get("/api/workspaces/test-ws/ls?recursive=true")

    assert response.status_code == 200
    assert "empty-dir" in response.json()["folders"]
