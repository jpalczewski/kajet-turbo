def test_create_workspace(auth_client):
    r = auth_client.post("/api/workspaces", json={"name": "new-ws"})
    assert r.status_code == 201
    assert r.json() == {"name": "new-ws"}


def test_create_workspace_already_exists_409(auth_client):
    r = auth_client.post("/api/workspaces", json={"name": "test-ws"})
    assert r.status_code == 409
    assert r.json()["error"] == "WORKSPACE_ALREADY_EXISTS"


def test_create_workspace_invalid_name_422(auth_client):
    r = auth_client.post("/api/workspaces", json={"name": "moje notatki"})
    assert r.status_code == 422
    assert r.json()["error"] == "WORKSPACE_INVALID_INPUT"


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
