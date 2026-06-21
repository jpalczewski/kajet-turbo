def test_get_note_chunks_returns_preview(auth_client):
    client, note_svc, ws_path = auth_client
    res = note_svc.save(
        "u1", "test-ws", ws_path, "Recipes", "# Recipes\n\n## Soup\n\ntomato soup\n", []
    )
    note_id = res["note_id"]
    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/chunks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["note_id"] == note_id
    assert body["chunk_count"] >= 1
    item = body["chunks"][0]
    assert {
        "ordinal",
        "header_path",
        "content",
        "embedded_text",
        "char_count",
        "embedded",
    } <= set(item)
    assert item["embedded"] is False  # nothing embedded in tests (FTS-only)


def test_get_note_chunks_404_unknown(auth_client):
    client, _svc, _ws = auth_client
    assert client.get("/api/workspaces/test-ws/notes/nope/chunks").status_code == 404


def test_get_note_chunks_403_no_access(no_access_client):
    assert no_access_client.get("/api/workspaces/test-ws/notes/x/chunks").status_code == 403


def test_get_note_chunks_401_anon(anon_client):
    assert anon_client.get("/api/workspaces/test-ws/notes/x/chunks").status_code == 401
