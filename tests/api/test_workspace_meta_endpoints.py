def test_list_includes_meta_defaults(auth_client):
    r = auth_client.get("/api/workspaces")
    assert r.status_code == 200
    ws = next(w for w in r.json()["workspaces"] if w["name"] == "test-ws")
    assert ws["description"] == "" and ws["folder"] == "" and ws["tags"] == []


def test_patch_sets_metadata(auth_client):
    r = auth_client.patch(
        "/api/workspaces/test-ws",
        json={"description": "Projekt X", "folder": "Praca", "tags": ["#Klient"]},
    )
    assert r.status_code == 200
    assert r.json() == {
        "name": "test-ws",
        "description": "Projekt X",
        "folder": "Praca",
        "tags": ["klient"],
    }
    listed = next(
        w for w in auth_client.get("/api/workspaces").json()["workspaces"] if w["name"] == "test-ws"
    )
    assert listed["description"] == "Projekt X" and listed["folder"] == "Praca"


def test_patch_partial_keeps_other_fields(auth_client):
    auth_client.patch("/api/workspaces/test-ws", json={"description": "d", "folder": "A"})
    r = auth_client.patch("/api/workspaces/test-ws", json={"tags": ["t"]})
    assert r.json()["folder"] == "A" and r.json()["description"] == "d"


def test_patch_invalid_tag_422(auth_client):
    r = auth_client.patch("/api/workspaces/test-ws", json={"tags": ["bad tag"]})
    assert r.status_code == 422


def test_patch_no_access_403(no_access_client):
    r = no_access_client.patch("/api/workspaces/test-ws", json={"description": "x"})
    assert r.status_code == 403
