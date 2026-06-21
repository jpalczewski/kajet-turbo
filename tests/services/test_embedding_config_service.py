import json

from kajet_turbo.services.embedding_config import EmbeddingConfigService
from sqlmodel import Session

from kajet_turbo.embedding.crypto import KeyCipher
from kajet_turbo.embedding.registry import load_registry
from kajet_turbo.models import User
from kajet_turbo.repositories.embedding_config import EmbeddingConfigRepository

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
                "mmlw": {"type": "hf", "model": "mmlw", "dim": 1024, "base_url": "https://hf"},
            }
        ),
        "EMBEDDING_DEFAULT_BACKEND": "openai-large",
    }
)
_CIPHER = KeyCipher("server-secret")


def _svc(database):
    return EmbeddingConfigService(_REG, EmbeddingConfigRepository(database.engine), lambda: _CIPHER)


def _user(database, uid="u1"):
    with Session(database.engine) as s:
        s.add(User(id=uid, email=f"{uid}@e.com", created_at="2026-01-01"))
        s.commit()


def test_list_backends_no_selection(database):
    _user(database)
    out = _svc(database).list_backends("u1")
    assert {b["backend_id"] for b in out["backends"]} == {"openai-large", "mmlw"}
    assert out["default_id"] == "openai-large"
    assert out["selected"] is None
    assert out["has_key"] is False
    assert all("api_key" not in b and "base_url" in b for b in out["backends"])


def test_list_backends_reflects_selection_and_key(database):
    _user(database)
    svc = _svc(database)
    svc.set_config("u1", backend_id="mmlw", api_key="sk-user")
    out = svc.list_backends("u1")
    assert out["selected"] == "mmlw"
    assert out["has_key"] is True
