from kajet_turbo.crypto import cipher_for
from kajet_turbo.embedding.resolver import ProfileResolver
from kajet_turbo.repositories.embedding_profiles import EmbeddingProfileRepository
from tests.conftest import seed_user

_CIPHER = cipher_for("embedding", secret="server-secret")


def _resolver(database):
    return ProfileResolver(EmbeddingProfileRepository(database.engine), lambda: _CIPHER)


def test_no_profile_returns_none(database):
    seed_user(database, "u1")
    assert _resolver(database).resolve_backend("u1") is None


def test_resolves_active_profile(database):
    seed_user(database, "u1")
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
    seed_user(database, "u1")
    EmbeddingProfileRepository(database.engine).create(
        "u1", "local", "http://local/v1", "m", None, 8
    )
    cfg = _resolver(database).resolve_backend("u1")
    assert cfg.api_key is None and cfg.base_url == "http://local/v1"
