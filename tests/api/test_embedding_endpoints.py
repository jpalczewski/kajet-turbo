import json

from fastapi import FastAPI
from sqlmodel import Session
from starlette.testclient import TestClient

from kajet_turbo.api.embedding import router
from kajet_turbo.dependencies import get_embedding_config_service
from kajet_turbo.embedding.crypto import KeyCipher
from kajet_turbo.embedding.registry import load_registry
from kajet_turbo.models import User
from kajet_turbo.repositories.embedding_config import EmbeddingConfigRepository
from kajet_turbo.services.embedding_config import EmbeddingConfigService

_REG = load_registry(
    {
        "EMBEDDING_BACKENDS": json.dumps(
            {
                "openai-large": {
                    "type": "openai",
                    "model": "text-embedding-3-large",
                    "dim": 3072,
                    "base_url": "https://api.openai.com/v1",
                },
            }
        ),
        "EMBEDDING_DEFAULT_BACKEND": "openai-large",
    }
)


def _app(database, monkeypatch, *, user_id="u1"):
    with Session(database.engine) as s:
        if user_id:
            s.add(User(id=user_id, email="u@e.com", created_at="2026-01-01"))
            s.commit()
    svc = EmbeddingConfigService(
        _REG,
        EmbeddingConfigRepository(database.engine),
        lambda: KeyCipher("server-secret"),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_embedding_config_service] = lambda: svc
    monkeypatch.setattr(
        "kajet_turbo.api.embedding.get_session_user",
        lambda _r: {"id": user_id} if user_id else None,
    )
    return TestClient(app), svc


def test_backends_requires_auth(database, monkeypatch):
    client, _ = _app(database, monkeypatch, user_id=None)
    assert client.get("/api/embedding/backends").status_code == 401


def test_backends_lists_registry(database, monkeypatch):
    client, _ = _app(database, monkeypatch)
    resp = client.get("/api/embedding/backends")
    assert resp.status_code == 200
    body = resp.json()
    assert {"backends", "default_id", "selected", "has_key"} <= set(body)
    assert {b["backend_id"] for b in body["backends"]} == {"openai-large"}
    assert body["has_key"] is False
