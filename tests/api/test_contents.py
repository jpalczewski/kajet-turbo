from pathlib import Path

import pytest


def test_contents_root_returns_folders_and_notes(auth_client):
    client, note_service, workspace = auth_client
    note_service.save("u1", "test-ws", workspace, "Root Note", "content", [])
    note_service.save("u1", "test-ws", workspace, "Deep Note", "content", [], folder="docs")

    response = client.get("/api/workspaces/test-ws/contents")

    assert response.status_code == 200
    data = response.json()
    assert data["resolution"] == "folder"
    assert data["folder_path"] == ""
    assert "docs" in data["folders"]
    assert data["child_folders"] == ["docs"]
    assert data["notes"][0]["title"] == "Root Note"
    assert {"size_bytes", "note_id"} <= data["notes"][0].keys()


def test_contents_subfolder_returns_folder_notes(auth_client):
    client, note_service, workspace = auth_client
    note_service.save("u1", "test-ws", workspace, "Doc", "content", [], folder="docs")
    note_service.save("u1", "test-ws", workspace, "Root", "content", [])

    response = client.get("/api/workspaces/test-ws/contents?path=docs")

    assert response.status_code == 200
    data = response.json()
    assert data["resolution"] == "folder"
    assert data["folder_path"] == "docs"
    assert [entry["title"] for entry in data["notes"]] == ["Doc"]


def test_contents_note_path_resolves_without_404(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.save("u1", "test-ws", workspace, "Doc", "content", [], folder="docs")[
        "note_id"
    ]

    response = client.get(f"/api/workspaces/test-ws/contents?path=docs/{note_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["resolution"] == "note"
    assert data["folder_path"] == "docs"
    assert data["selected_note_id"] == note_id
    assert [entry["note_id"] for entry in data["notes"]] == [note_id]


def test_contents_missing_path_returns_missing_without_404(auth_client):
    response = auth_client.get("/api/workspaces/test-ws/contents?path=missing")

    assert response.status_code == 200
    data = response.json()
    assert data["resolution"] == "missing"
    assert data["selected_note_id"] is None


def test_contents_sets_default_note_for_readme(auth_client):
    client, note_service, workspace = auth_client
    readme_id = note_service.save("u1", "test-ws", workspace, "README", "intro", [], folder="docs")[
        "note_id"
    ]
    note_service.save("u1", "test-ws", workspace, "Other", "content", [], folder="docs")

    response = client.get("/api/workspaces/test-ws/contents?path=docs")

    assert response.status_code == 200
    data = response.json()
    assert data["resolution"] == "folder"
    assert data["default_note_id"] == readme_id


def test_contents_invalid_path_returns_400(auth_client):
    response = auth_client.get("/api/workspaces/test-ws/contents?path=../evil")

    assert response.status_code == 400


@pytest.mark.parametrize(
    ("client_fixture", "expected_status"),
    [("anon_client", 401), ("no_access_client", 403)],
)
def test_contents_requires_authorized_workspace(request, client_fixture, expected_status):
    client = request.getfixturevalue(client_fixture)

    assert client.get("/api/workspaces/test-ws/contents").status_code == expected_status


def test_contents_includes_empty_folder(auth_client):
    client, _, workspace = auth_client
    gitkeep = Path(workspace) / "empty-dir" / ".gitkeep"
    gitkeep.parent.mkdir(parents=True)
    gitkeep.touch()

    response = client.get("/api/workspaces/test-ws/contents")

    assert response.status_code == 200
    assert "empty-dir" in response.json()["folders"]
