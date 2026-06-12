import pytest
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from pydantic import AnyUrl

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
    from datetime import UTC, datetime

    from kajet_turbo.db import Database
    from kajet_turbo.repositories.oauth import OAuthRepository

    db = Database(str(tmp_path / "test.db"))
    oauth = OAuthRepository(db.engine)
    oauth.save_oauth_client(
        client_id="test-client",
        client_secret="secret123",
        redirect_uris=["https://example.com/callback"],
        created_at=datetime.now(UTC).isoformat(),
    )
    client = oauth.get_oauth_client("test-client")
    assert client is not None
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


def test_delete_expired_tokens_leaves_valid_and_null_expiry(tmp_path):
    import time

    from kajet_turbo.db import Database
    from kajet_turbo.repositories.oauth import OAuthRepository

    db = Database(str(tmp_path / "test.db"))
    repo = OAuthRepository(db.engine)
    now = int(time.time())

    repo.upsert_access_token("at_expired", "c1", [], now - 10)
    repo.upsert_access_token("at_valid", "c1", [], now + 3600)
    repo.upsert_refresh_token("rt_expired", "c1", [], now - 10)
    repo.upsert_refresh_token("rt_null", "c1", [], None)

    repo.delete_expired_tokens()

    valid_ats = {r["token"] for r in repo.get_valid_access_tokens()}
    valid_rts = {r["token"] for r in repo.get_valid_refresh_tokens()}
    assert "at_expired" not in valid_ats
    assert "at_valid" in valid_ats
    assert "rt_expired" not in valid_rts
    assert "rt_null" in valid_rts
    db.close()


def test_delete_individual_tokens(tmp_path):
    import time

    from kajet_turbo.db import Database
    from kajet_turbo.repositories.oauth import OAuthRepository

    db = Database(str(tmp_path / "test.db"))
    repo = OAuthRepository(db.engine)
    now = int(time.time())

    repo.upsert_access_token("at1", "c1", [], now + 3600)
    repo.upsert_refresh_token("rt1", "c1", [], None)
    repo.delete_access_token("at1")
    repo.delete_refresh_token("rt1")

    assert repo.get_valid_access_tokens() == []
    assert repo.get_valid_refresh_tokens() == []
    db.close()


def test_expired_access_token_preserves_refresh_token(monkeypatch, tmp_path):
    """AT expiry must NOT wipe the paired RT — client needs RT to refresh."""
    import asyncio
    import time

    from mcp.server.auth.provider import AccessToken, RefreshToken
    from mcp.server.auth.settings import ClientRegistrationOptions

    from kajet_turbo.auth import KajetOAuthProvider
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.oauth import OAuthRepository

    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    db = Database(str(tmp_path / "test.db"))
    repo = OAuthRepository(db.engine)
    now = int(time.time())

    provider = KajetOAuthProvider(
        oauth_repo=repo,
        base_url="http://localhost:8000/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )

    # Inject expired AT + valid RT directly into memory (simulates in-session expiry)
    rt_val = "rt_valid_xyz"
    at_val = "at_expired_xyz"
    provider.refresh_tokens[rt_val] = RefreshToken(
        token=rt_val, client_id="client1", scopes=["read"], expires_at=None
    )
    provider.access_tokens[at_val] = AccessToken(
        token=at_val, client_id="client1", scopes=["read"], expires_at=now - 10
    )
    provider._access_to_refresh_map[at_val] = rt_val
    provider._refresh_to_access_map[rt_val] = at_val

    result = asyncio.run(provider.verify_token(at_val))
    assert result is None

    assert rt_val in provider.refresh_tokens, "RT must survive AT expiry so client can refresh"
    db.close()


def test_exchange_refresh_token_deletes_old_tokens_from_db(monkeypatch, tmp_path):
    import asyncio
    import time

    from mcp.server.auth.settings import ClientRegistrationOptions
    from mcp.shared.auth import OAuthClientInformationFull

    from kajet_turbo.auth import KajetOAuthProvider
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.oauth import OAuthRepository

    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    db = Database(str(tmp_path / "test.db"))
    repo = OAuthRepository(db.engine)

    now = int(time.time())
    old_rt = "old_refresh_token_xyz"
    old_at = "old_access_token_xyz"
    repo.upsert_refresh_token(old_rt, "client1", ["read"], None)
    repo.upsert_access_token(old_at, "client1", ["read"], now + 3600, old_rt)

    provider = KajetOAuthProvider(
        oauth_repo=repo,
        base_url="http://localhost:8000/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )
    assert old_rt in provider.refresh_tokens
    assert old_at in provider.access_tokens

    client = OAuthClientInformationFull(
        client_id="client1",
        redirect_uris=[AnyUrl("http://localhost/callback")],
    )
    provider.clients["client1"] = client
    old_refresh_obj = provider.refresh_tokens[old_rt]

    asyncio.run(provider.exchange_refresh_token(client, old_refresh_obj, ["read"]))

    valid_ats = {r["token"] for r in repo.get_valid_access_tokens()}
    valid_rts = {r["token"] for r in repo.get_valid_refresh_tokens()}
    assert old_at not in valid_ats, "stary AT musi być usunięty z DB po rotacji"
    assert old_rt not in valid_rts, "stary RT musi być usunięty z DB po rotacji"

    # Drugi provider symuluje restart — stare tokeny nie mogą wrócić do pamięci
    provider2 = KajetOAuthProvider(
        oauth_repo=repo,
        base_url="http://localhost:8000/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )
    assert old_at not in provider2.access_tokens
    assert old_rt not in provider2.refresh_tokens
    db.close()
