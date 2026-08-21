def test_delete_no_access_403(no_access_client):
    r = no_access_client.delete("/api/workspaces/test-ws")
    assert r.status_code == 403


def test_delete_removes_workspace_from_list_and_directory(auth_client):
    r = auth_client.delete("/api/workspaces/test-ws")
    assert r.status_code == 200
    assert r.json() == {"name": "test-ws"}

    listed = auth_client.get("/api/workspaces").json()["workspaces"]
    assert all(w["name"] != "test-ws" for w in listed)
    assert not auth_client.workspace.exists()


def test_delete_twice_403_second_time(auth_client):
    # First delete revokes access, so has_access() -> False afterward; a second
    # call correctly 403s rather than re-running delete against a gone workspace.
    assert auth_client.delete("/api/workspaces/test-ws").status_code == 200
    assert auth_client.delete("/api/workspaces/test-ws").status_code == 403
