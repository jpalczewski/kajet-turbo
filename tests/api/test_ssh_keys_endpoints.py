from starlette.testclient import TestClient

from kajet_turbo.api.schemas.ssh_keys import SSH_KEY_ALGORITHMS
from kajet_turbo.api.ssh_keys import router
from kajet_turbo.crypto import cipher_for
from kajet_turbo.crypto.ssh_keys import ALGORITHMS
from kajet_turbo.dependencies import CurrentUser, get_required_user, get_ssh_key_service
from kajet_turbo.repositories.ssh_keys import SshKeyRepository
from kajet_turbo.services.ssh_keys import SshKeyService
from tests.api.conftest import build_test_app
from tests.conftest import seed_user


def test_schema_algorithms_match_crypto_module():
    # CreateSshKeyRequest.algorithm is a Literal (see schemas/ssh_keys.py comment on why
    # it can't just reference ALGORITHMS directly) -- this keeps the two lists in sync.
    assert set(SSH_KEY_ALGORITHMS) == set(ALGORITHMS)


def _app(database, monkeypatch, *, user_id="u1"):
    if user_id:
        seed_user(database, user_id)
    svc = SshKeyService(
        SshKeyRepository(database.engine),
        cipher_factory=lambda: cipher_for("ssh-key", secret="server-secret"),
    )
    app = build_test_app(routers=(router,))
    app.dependency_overrides[get_ssh_key_service] = lambda: svc
    if user_id:
        app.dependency_overrides[get_required_user] = lambda: CurrentUser(
            id=user_id, email="", timezone="", locale=""
        )
    return TestClient(app)


def test_list_create_delete_flow(database, monkeypatch):
    client = _app(database, monkeypatch)
    assert client.get("/api/me/ssh-keys").json() == {"keys": []}

    created = client.post("/api/me/ssh-keys", json={"name": "laptop", "algorithm": "ed25519"})
    assert created.status_code == 201
    body = created.json()
    assert body["public_key"].startswith("ssh-ed25519 ")
    # private key never leaves the server
    assert "PRIVATE" not in created.text and "private" not in body

    listed = client.get("/api/me/ssh-keys").json()["keys"]
    assert [k["name"] for k in listed] == ["laptop"]

    deleted = client.delete(f"/api/me/ssh-keys/{body['id']}")
    assert deleted.status_code == 200
    assert client.get("/api/me/ssh-keys").json() == {"keys": []}
    assert client.delete(f"/api/me/ssh-keys/{body['id']}").status_code == 404


def test_duplicate_name_returns_409(database, monkeypatch):
    client = _app(database, monkeypatch)
    client.post("/api/me/ssh-keys", json={"name": "laptop", "algorithm": "ed25519"})
    dup = client.post("/api/me/ssh-keys", json={"name": "laptop", "algorithm": "ed25519"})
    assert dup.status_code == 409
    assert dup.json()["error"] == "SSH_KEY_NAME_TAKEN"


def test_unknown_algorithm_returns_422(database, monkeypatch):
    # Migrated to a typed body (#254): an invalid Literal value now 422s from Pydantic
    # before the route runs, instead of a service-level 400.
    client = _app(database, monkeypatch)
    bad = client.post("/api/me/ssh-keys", json={"name": "x", "algorithm": "dsa-1024"})
    assert bad.status_code == 422
    assert bad.json()["error"] == "SSH_KEY_INVALID_ALGORITHM"


def test_blank_name_returns_422(database, monkeypatch):
    # Both an empty string and a whitespace-only string are "no name given" and must get
    # the same code -- name has no `min_length` precisely so "" doesn't take a different
    # path (generic INVALID_INPUT via min_length) than "   " (the field_validator below).
    client = _app(database, monkeypatch)
    for name in ("", "   "):
        r = client.post("/api/me/ssh-keys", json={"name": name, "algorithm": "ed25519"})
        assert r.status_code == 422
        assert r.json()["error"] == "SSH_KEY_NAME_REQUIRED"


def test_missing_fields_returns_422(database, monkeypatch):
    # Regression: a missing "name" must fall back to generic INVALID_INPUT, not
    # SSH_KEY_NAME_REQUIRED -- CreateSshKeyRequest.legacy_error_codes (api/errors.py)
    # deliberately doesn't declare "name"; only the "algorithm" field does.
    client = _app(database, monkeypatch)
    r = client.post("/api/me/ssh-keys", json={})
    assert r.status_code == 422
    assert r.json()["error"] == "INVALID_INPUT"


def test_delete_unknown_key_returns_404_with_code(database, monkeypatch):
    client = _app(database, monkeypatch)
    r = client.delete("/api/me/ssh-keys/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"] == "SSH_KEY_NOT_FOUND"


def test_create_response_never_contains_private_key_material(database, monkeypatch):
    # Regression for #254: the created key's private key must never appear in the
    # response body, success or error, and never in a logger call this route touches.
    client = _app(database, monkeypatch)
    created = client.post("/api/me/ssh-keys", json={"name": "laptop", "algorithm": "ed25519"})
    assert set(created.json().keys()) == {
        "id",
        "name",
        "algorithm",
        "fingerprint",
        "public_key",
        "created_at",
    }
    dup = client.post("/api/me/ssh-keys", json={"name": "laptop", "algorithm": "ed25519"})
    assert "PRIVATE" not in dup.text
    bad = client.post("/api/me/ssh-keys", json={"name": "", "algorithm": "ed25519"})
    assert "PRIVATE" not in bad.text


def test_list_response_matches_response_model(database, monkeypatch):
    # Regression: GET must return the typed SshKeysResponse/SshKeyItem shape exactly
    # (was previously a raw JSONResponse bypassing response_model validation/filtering).
    client = _app(database, monkeypatch)
    client.post("/api/me/ssh-keys", json={"name": "laptop", "algorithm": "ed25519"})
    listed = client.get("/api/me/ssh-keys").json()
    assert set(listed.keys()) == {"keys"}
    assert set(listed["keys"][0].keys()) == {
        "id",
        "name",
        "algorithm",
        "fingerprint",
        "public_key",
        "created_at",
    }


def test_requires_login(database, monkeypatch):
    client = _app(database, monkeypatch, user_id=None)
    assert client.get("/api/me/ssh-keys").status_code == 401
    r = client.post("/api/me/ssh-keys", json={"name": "x", "algorithm": "ed25519"})
    assert r.status_code == 401
    assert client.delete("/api/me/ssh-keys/does-not-matter").status_code == 401
