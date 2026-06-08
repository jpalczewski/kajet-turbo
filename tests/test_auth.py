import pytest
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider

from kajet_turbo.auth import KajetOAuthProvider


def test_create_auth_returns_in_memory_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    from kajet_turbo.auth import create_auth
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.oauth import OAuthRepository

    db = Database(str(tmp_path / "test.db"))
    provider = create_auth(OAuthRepository(db.engine))
    db.close()

    assert isinstance(provider, InMemoryOAuthProvider)
    assert isinstance(provider, KajetOAuthProvider)


def test_create_auth_requires_base_url(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    monkeypatch.delenv("COOLIFY_FQDN", raising=False)
    monkeypatch.delenv("COOLIFY_URL", raising=False)
    from kajet_turbo.auth import create_auth
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.oauth import OAuthRepository

    db = Database(str(tmp_path / "test.db"))
    with pytest.raises(RuntimeError):
        create_auth(OAuthRepository(db.engine))
    db.close()


def test_create_auth_fallback_coolify_fqdn(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    monkeypatch.setenv("COOLIFY_FQDN", "preview.example.com")
    monkeypatch.delenv("COOLIFY_URL", raising=False)
    from kajet_turbo.auth import create_auth
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.oauth import OAuthRepository

    db = Database(str(tmp_path / "test.db"))
    provider = create_auth(OAuthRepository(db.engine))
    db.close()

    assert isinstance(provider, InMemoryOAuthProvider)


def test_create_auth_fallback_coolify_fqdn_with_protocol(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    monkeypatch.setenv("COOLIFY_FQDN", "https://preview.example.com")
    monkeypatch.delenv("COOLIFY_URL", raising=False)
    from kajet_turbo.auth import create_auth
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.oauth import OAuthRepository

    db = Database(str(tmp_path / "test.db"))
    provider = create_auth(OAuthRepository(db.engine))
    db.close()

    assert isinstance(provider, InMemoryOAuthProvider)


def test_create_auth_fallback_coolify_url(monkeypatch, tmp_path):
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    monkeypatch.delenv("COOLIFY_FQDN", raising=False)
    monkeypatch.setenv("COOLIFY_URL", "preview.example.com")
    from kajet_turbo.auth import create_auth
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.oauth import OAuthRepository

    db = Database(str(tmp_path / "test.db"))
    provider = create_auth(OAuthRepository(db.engine))
    db.close()

    assert isinstance(provider, InMemoryOAuthProvider)


def test_save_and_get_oauth_client(tmp_path):
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.oauth import OAuthRepository
    from datetime import UTC, datetime

    db = Database(str(tmp_path / "test.db"))
    oauth = OAuthRepository(db.engine)
    oauth.save_oauth_client(
        client_id="test-client",
        client_secret="secret123",
        redirect_uris=["https://example.com/callback"],
        created_at=datetime.now(UTC).isoformat(),
    )
    client = oauth.get_oauth_client("test-client")
    assert client["client_id"] == "test-client"
    assert client["client_secret"] == "secret123"
    assert "https://example.com/callback" in client["redirect_uris"]
    db.close()


def test_get_oauth_client_returns_none_for_missing(tmp_path):
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.oauth import OAuthRepository

    db = Database(str(tmp_path / "test.db"))
    oauth = OAuthRepository(db.engine)
    assert oauth.get_oauth_client("nie-istnieje") is None
    db.close()


def test_hash_and_verify_password():
    from kajet_turbo.auth import hash_password, verify_password

    hashed = hash_password("secret123")
    assert verify_password(hashed, "secret123") is True
    assert verify_password(hashed, "wrong") is False


def test_verify_password_none_hash():
    from kajet_turbo.auth import verify_password

    assert verify_password("", "anything") is False
