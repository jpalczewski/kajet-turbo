from sqlmodel import Session

from kajet_turbo.embedding.crypto import KeyCipher
from kajet_turbo.embedding.resolver import ProfileResolver
from kajet_turbo.models import User
from kajet_turbo.repositories.embedding_profiles import EmbeddingProfileRepository

_CIPHER = KeyCipher("server-secret")


def _user(database, uid="u1"):
    with Session(database.engine) as s:
        s.add(User(id=uid, email=f"{uid}@e.com", created_at="2026-01-01"))
        s.commit()


def _resolver(database):
    return ProfileResolver(EmbeddingProfileRepository(database.engine), lambda: _CIPHER)


def test_no_profile_returns_none(database):
    _user(database)
    assert _resolver(database).resolve_backend("u1") is None


def test_resolves_active_profile(database):
    _user(database)
    repo = EmbeddingProfileRepository(database.engine)
    repo.create(
        "u1",
        "P",
        "https://api.openai.com/v1",
        "text-embedding-3-large",
        _CIPHER.encrypt("sk-user"),
        3072,
    )
    cfg = _resolver(database).resolve_backend("u1")
    assert cfg.type == "openai"
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.model == "text-embedding-3-large"
    assert cfg.dim == 3072
    assert cfg.api_key == "sk-user"
    assert cfg.backend_id == "https://api.openai.com/v1"  # cache identity = base_url


def test_keyless_profile_yields_none_key(database):
    _user(database)
    EmbeddingProfileRepository(database.engine).create(
        "u1", "local", "http://local/v1", "m", None, 8
    )
    cfg = _resolver(database).resolve_backend("u1")
    assert cfg.api_key is None and cfg.base_url == "http://local/v1"
