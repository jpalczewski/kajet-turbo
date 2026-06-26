from fastapi import FastAPI
from sqlmodel import Session
from starlette.testclient import TestClient

from kajet_turbo.api.ssh_keys import router
from kajet_turbo.crypto import cipher_for
from kajet_turbo.dependencies import get_ssh_key_service
from kajet_turbo.models import User
from kajet_turbo.repositories.ssh_keys import SshKeyRepository
from kajet_turbo.services.ssh_keys import SshKeyService


def _app(database, monkeypatch, *, user_id="u1"):
    if user_id:
        with Session(database.engine) as s:
            s.add(User(id=user_id, email="u@e.com", created_at="2026-01-01"))
            s.commit()
    svc = SshKeyService(
        SshKeyRepository(database.engine),
        cipher_factory=lambda: cipher_for("ssh-key", secret="server-secret"),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_ssh_key_service] = lambda: svc
    monkeypatch.setattr(
        "kajet_turbo.api.ssh_keys.get_session_user",
        lambda _r: {"id": user_id} if user_id else None,
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


def test_unknown_algorithm_returns_400(database, monkeypatch):
    client = _app(database, monkeypatch)
    bad = client.post("/api/me/ssh-keys", json={"name": "x", "algorithm": "dsa-1024"})
    assert bad.status_code == 400


def test_requires_login(database, monkeypatch):
    client = _app(database, monkeypatch, user_id=None)
    assert client.get("/api/me/ssh-keys").status_code == 401
    r = client.post("/api/me/ssh-keys", json={"name": "x", "algorithm": "ed25519"})
    assert r.status_code == 401
