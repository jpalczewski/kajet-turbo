def test_reindex_endpoint_runs(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "A", "# A\n\nbody\n", [])
    resp = client.post("/api/workspaces/test-ws/reindex")
    assert resp.status_code == 200
    assert resp.json()["count"] >= 1


def test_reindex_403_no_access(no_access_client):
    assert no_access_client.post("/api/workspaces/test-ws/reindex").status_code == 403


def test_reindex_401_anon(anon_client):
    assert anon_client.post("/api/workspaces/test-ws/reindex").status_code == 401
