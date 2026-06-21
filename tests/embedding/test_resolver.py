import json

from sqlmodel import Session

from kajet_turbo.embedding.crypto import KeyCipher
from kajet_turbo.embedding.registry import load_registry
from kajet_turbo.embedding.resolver import BackendResolver
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
                "mmlw": {
                    "type": "hf",
                    "model": "mmlw",
                    "dim": 1024,
                    "base_url": "https://hf",
                    "query_prefix": "zapytanie: ",
                },
            }
        ),
        "EMBEDDING_DEFAULT_BACKEND": "openai-large",
    }
)
_CIPHER = KeyCipher("server-secret")


def _user(database, user_id="u1"):
    with Session(database.engine) as session:
        session.add(User(id=user_id, email=f"{user_id}@example.com", created_at="2026-01-01"))
        session.commit()


def _resolver(database, *, fallback=None):
    return BackendResolver(
        registry=_REG,
        config_repo=EmbeddingConfigRepository(database.engine),
        cipher=_CIPHER,
        instance_fallback_key=fallback,
    )


def test_no_config_uses_default_backend(database):
    _user(database)
    cfg = _resolver(database, fallback="sk-fallback").resolve_backend("u1")
    assert cfg.backend_id == "openai-large"
    assert cfg.api_key == "sk-fallback"


def test_user_selection_and_sealed_key(database):
    _user(database)
    repo = EmbeddingConfigRepository(database.engine)
    repo.upsert("u1", backend_id="mmlw", api_key_enc=_CIPHER.encrypt("sk-user"))
    cfg = _resolver(database).resolve_backend("u1")
    assert cfg.backend_id == "mmlw"
    assert cfg.model == "mmlw"
    assert cfg.dim == 1024
    assert cfg.query_prefix == "zapytanie: "
    assert cfg.api_key == "sk-user"


def test_user_key_overrides_instance_fallback(database):
    _user(database)
    repo = EmbeddingConfigRepository(database.engine)
    repo.upsert("u1", backend_id="openai-large", api_key_enc=_CIPHER.encrypt("sk-user"))
    cfg = _resolver(database, fallback="sk-fallback").resolve_backend("u1")
    assert cfg.api_key == "sk-user"


def test_unknown_selection_falls_back_to_default(database):
    _user(database)
    repo = EmbeddingConfigRepository(database.engine)
    repo.upsert("u1", backend_id="ghost", api_key_enc=None)
    cfg = _resolver(database, fallback="sk-fallback").resolve_backend("u1")
    assert cfg.backend_id == "openai-large"


def test_no_key_anywhere_yields_config_with_none_key(database):
    _user(database)
    cfg = _resolver(database).resolve_backend("u1")  # no fallback, no user key
    assert cfg.backend_id == "openai-large"
    assert cfg.api_key is None  # callers degrade to FTS-only


def test_empty_registry_returns_none(database):
    _user(database)
    resolver = BackendResolver(
        registry=load_registry({}),
        config_repo=EmbeddingConfigRepository(database.engine),
        cipher=_CIPHER,
    )
    assert resolver.resolve_backend("u1") is None
