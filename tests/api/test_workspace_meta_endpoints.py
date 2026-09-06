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


def test_patch_requires_auth_401(anon_client):
    r = anon_client.patch("/api/workspaces/test-ws", json={"description": "x"})
    assert r.status_code == 401


def test_patch_wrong_type_422(auth_client):
    # tags must be a list -- a string reaches Pydantic validation now, not an isinstance
    # guard that used to silently drop it (#254 behavior change, see rest-contracts.md).
    r = auth_client.patch("/api/workspaces/test-ws", json={"tags": "not-a-list"})
    assert r.status_code == 422


def test_patch_wrong_type_folder_is_workspace_invalid_input(auth_client):
    # UpdateWorkspaceRequest.folder is optional (unlike MoveNoteRequest's required,
    # same-named field) -- a wrong-type value must not 422 with the misleading
    # FOLDER_PATH_REQUIRED ("path is required") code MoveNoteRequest maps that field name
    # to. UpdateWorkspaceRequest's own legacy_error_codes entry (schemas/workspaces/meta.py)
    # resolves this model's "folder" independently, so the two can never collide.
    r = auth_client.patch("/api/workspaces/test-ws", json={"folder": 123})
    assert r.status_code == 422
    assert r.json()["error"] == "WORKSPACE_INVALID_INPUT"


def test_patch_omitted_key_is_a_no_op(auth_client):
    auth_client.patch("/api/workspaces/test-ws", json={"description": "d", "folder": "A"})
    r = auth_client.patch("/api/workspaces/test-ws", json={})
    assert r.status_code == 200
    assert r.json() == {"name": "test-ws", "description": "d", "folder": "A", "tags": []}


def test_patch_explicit_null_is_also_a_no_op(auth_client):
    # An explicit JSON null for a field carries the same "leave it unchanged" contract as
    # omitting the key entirely (see the route's own comment and rest-contracts.md) --
    # exercise that branch directly instead of only the omitted-key case above.
    auth_client.patch("/api/workspaces/test-ws", json={"description": "d", "folder": "A"})
    r = auth_client.patch("/api/workspaces/test-ws", json={"description": None, "folder": None})
    assert r.status_code == 200
    assert r.json() == {"name": "test-ws", "description": "d", "folder": "A", "tags": []}


def test_list_requires_auth_401(anon_client):
    r = anon_client.get("/api/workspaces")
    assert r.status_code == 401


def test_create_workspace_success(auth_client):
    r = auth_client.post("/api/workspaces", json={"name": "new-ws", "description": "hi"})
    assert r.status_code == 201
    assert r.json() == {"name": "new-ws"}


def test_create_workspace_requires_auth_401(anon_client):
    r = anon_client.post("/api/workspaces", json={"name": "new-ws"})
    assert r.status_code == 401


def test_create_workspace_missing_name_422(auth_client):
    r = auth_client.post("/api/workspaces", json={"description": "no name here"})
    assert r.status_code == 422
    assert r.json()["error"] == "WORKSPACE_NAME_REQUIRED"


def test_create_workspace_blank_name_422(auth_client):
    r = auth_client.post("/api/workspaces", json={"name": "   "})
    assert r.status_code == 422
    assert r.json()["error"] == "WORKSPACE_NAME_REQUIRED"


def test_create_workspace_null_name_422(auth_client):
    # str(None).strip() would be the non-blank string "None" -- covers the model_validator
    # branch that distinguishes an explicit JSON null from a present, non-empty value.
    r = auth_client.post("/api/workspaces", json={"name": None})
    assert r.status_code == 422
    assert r.json()["error"] == "WORKSPACE_NAME_REQUIRED"


def test_create_workspace_duplicate_409(auth_client):
    r = auth_client.post("/api/workspaces", json={"name": "test-ws"})
    assert r.status_code == 409
    assert r.json()["error"] == "WORKSPACE_ALREADY_EXISTS"


def test_create_workspace_wrong_type_folder_is_workspace_invalid_input(auth_client):
    r = auth_client.post("/api/workspaces", json={"name": "new-ws", "folder": 123})
    assert r.status_code == 422
    assert r.json()["error"] == "WORKSPACE_INVALID_INPUT"
