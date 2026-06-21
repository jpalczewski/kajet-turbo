from fastapi import FastAPI
from sqlmodel import Session
from starlette.testclient import TestClient

from kajet_turbo.api.embedding import router
from kajet_turbo.dependencies import get_embedding_profile_service
from kajet_turbo.embedding.crypto import KeyCipher
from kajet_turbo.models import User
from kajet_turbo.repositories.embedding_profiles import EmbeddingProfileRepository
from kajet_turbo.services.embedding_profiles import EmbeddingProfileService


def _app(database, monkeypatch, *, user_id="u1", probe_dim=3, probe_error=None):
    if user_id:
        with Session(database.engine) as s:
            s.add(User(id=user_id, email="u@e.com", created_at="2026-01-01"))
            s.commit()

    def probe(base_url, model, api_key):
        if probe_error:
            raise probe_error
        return probe_dim

    svc = EmbeddingProfileService(
        EmbeddingProfileRepository(database.engine),
        cipher_factory=lambda: KeyCipher("server-secret"),
        probe_dim=probe,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_embedding_profile_service] = lambda: svc
    monkeypatch.setattr(
        "kajet_turbo.api.embedding.get_session_user",
        lambda _r: {"id": user_id} if user_id else None,
    )
    return TestClient(app), svc


def test_list_requires_auth(database, monkeypatch):
    client, _ = _app(database, monkeypatch, user_id=None)
    assert client.get("/api/me/embedding-profiles").status_code == 401


def test_create_list_activate_flow(database, monkeypatch):
    client, _ = _app(database, monkeypatch, probe_dim=1024)
    r = client.post(
        "/api/me/embedding-profiles",
        json={"name": "mmlw", "base_url": "http://h/v1", "model": "m", "api_key": "sk-x"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["dim"] == 1024 and body["is_active"] is True and body["has_key"] is True
    assert "sk-x" not in r.text and "api_key" not in body

    listing = client.get("/api/me/embedding-profiles").json()["profiles"]
    assert len(listing) == 1 and listing[0]["id"] == body["id"]


def test_create_probe_failure_is_400(database, monkeypatch):
    client, _ = _app(database, monkeypatch, probe_error=RuntimeError("401 from embedder"))
    r = client.post(
        "/api/me/embedding-profiles",
        json={"name": "bad", "base_url": "http://h/v1", "model": "m", "api_key": "k"},
    )
    assert r.status_code == 400


def test_activate_unknown_is_404(database, monkeypatch):
    client, _ = _app(database, monkeypatch)
    assert client.post("/api/me/embedding-profiles/nope/activate").status_code == 404


def test_delete(database, monkeypatch):
    client, _ = _app(database, monkeypatch)
    pid = client.post(
        "/api/me/embedding-profiles",
        json={"name": "A", "base_url": "http://a/v1", "model": "m", "api_key": "k"},
    ).json()["id"]
    assert client.delete(f"/api/me/embedding-profiles/{pid}").status_code == 200
    assert client.get("/api/me/embedding-profiles").json()["profiles"] == []


def test_update_keeps_key_secret(database, monkeypatch):
    client, _ = _app(database, monkeypatch)
    pid = client.post(
        "/api/me/embedding-profiles",
        json={"name": "A", "base_url": "http://a/v1", "model": "m", "api_key": "sk-keep"},
    ).json()["id"]
    r = client.put(
        f"/api/me/embedding-profiles/{pid}",
        json={"name": "A2", "base_url": "http://a/v1", "model": "m"},
    )
    assert r.status_code == 200
    assert "sk-keep" not in r.text and r.json()["has_key"] is True
