from pathlib import Path

import pytest


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
