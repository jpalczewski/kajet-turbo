import pytest
from fastmcp.server.auth.auth import OAuthProvider
from pydantic import AnyUrl

from kajet_turbo.auth import KajetOAuthProvider


def test_create_auth_returns_oauth_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    from kajet_turbo.auth import create_auth
    from kajet_turbo.db import Database
    from kajet_turbo.repositories.oauth import OAuthRepository

    db = Database(str(tmp_path / "test.db"))
    provider = create_auth(OAuthRepository(db.engine))
    db.close()

    assert isinstance(provider, OAuthProvider)
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

    assert isinstance(provider, OAuthProvider)


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

    assert isinstance(provider, OAuthProvider)


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

    assert isinstance(provider, OAuthProvider)


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

    # Expired AT paired with a valid RT (simulates in-session expiry).
    rt_val = "rt_valid_xyz"
    at_val = "at_expired_xyz"
    repo.upsert_refresh_token(rt_val, "client1", ["read"], None)
    repo.upsert_access_token(at_val, "client1", ["read"], now - 10, rt_val)

    result = asyncio.run(provider.verify_token(at_val))
    assert result is None

    client = _make_client("client1")
    rt = asyncio.run(provider.load_refresh_token(client, rt_val))
    assert rt is not None, "RT must survive AT expiry so client can refresh"
    db.close()


# --- Split-brain tests: two provider instances sharing one DB simulate ---
# --- two uvicorn workers behind a round-robin proxy (MCP_WORKERS>1).    ---


def _make_split_brain_pair(tmp_path, monkeypatch):
    from mcp.server.auth.settings import ClientRegistrationOptions

    from kajet_turbo.db import Database
    from kajet_turbo.repositories.oauth import OAuthRepository

    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    db = Database(str(tmp_path / "test.db"))
    repo = OAuthRepository(db.engine)

    def make():
        return KajetOAuthProvider(
            oauth_repo=repo,
            base_url="http://localhost:8000/mcp",
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )

    return db, repo, make


def _make_client(client_id="client-a"):
    from mcp.shared.auth import OAuthClientInformationFull

    return OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=[AnyUrl("http://localhost/callback")],
        scope="read",
    )


def _auth_params():
    from mcp.server.auth.provider import AuthorizationParams

    return AuthorizationParams(
        state="state-xyz",
        scopes=["read"],
        code_challenge="challenge123",
        redirect_uri=AnyUrl("http://localhost/callback"),
        redirect_uri_provided_explicitly=True,
    )


def _issue_code(provider, client):
    """Run authorize + login-completion on one provider, return the auth code value."""
    import asyncio
    from urllib.parse import parse_qs, urlparse

    login_url = asyncio.run(provider.authorize(client, _auth_params()))
    pending_id = login_url.split("pending=")[1]
    redirect = asyncio.run(provider.complete_authorization(pending_id))
    return parse_qs(urlparse(redirect).query)["code"][0]


def test_client_registered_on_one_worker_visible_on_another(monkeypatch, tmp_path):
    import asyncio

    db, _repo, make = _make_split_brain_pair(tmp_path, monkeypatch)
    worker_a, worker_b = make(), make()

    asyncio.run(worker_a.register_client(_make_client()))

    found = asyncio.run(worker_b.get_client("client-a"))
    assert found is not None, "client registered on worker A must be visible on worker B"
    assert found.client_id == "client-a"
    db.close()


def test_auth_code_created_on_one_worker_exchangeable_on_another(monkeypatch, tmp_path):
    import asyncio

    db, _repo, make = _make_split_brain_pair(tmp_path, monkeypatch)
    worker_a, worker_b = make(), make()
    client = _make_client()
    asyncio.run(worker_a.register_client(client))

    code_value = _issue_code(worker_a, client)

    code_obj = asyncio.run(worker_b.load_authorization_code(client, code_value))
    assert code_obj is not None, "auth code created on worker A must load on worker B"

    token = asyncio.run(worker_b.exchange_authorization_code(client, code_obj))
    assert token.access_token
    assert token.refresh_token
    db.close()


def test_auth_code_is_single_use_across_workers(monkeypatch, tmp_path):
    import asyncio

    from mcp.server.auth.provider import TokenError

    db, _repo, make = _make_split_brain_pair(tmp_path, monkeypatch)
    worker_a, worker_b = make(), make()
    client = _make_client()
    asyncio.run(worker_a.register_client(client))

    code_value = _issue_code(worker_a, client)
    code_obj_b = asyncio.run(worker_b.load_authorization_code(client, code_value))
    assert code_obj_b is not None
    asyncio.run(worker_b.exchange_authorization_code(client, code_obj_b))

    # Replay on the worker that CREATED the code must also fail.
    code_obj_a = asyncio.run(worker_a.load_authorization_code(client, code_value))
    if code_obj_a is not None:
        with pytest.raises(TokenError):
            asyncio.run(worker_a.exchange_authorization_code(client, code_obj_a))
    db.close()


def test_access_token_issued_on_one_worker_valid_on_another(monkeypatch, tmp_path):
    import asyncio

    db, _repo, make = _make_split_brain_pair(tmp_path, monkeypatch)
    worker_a, worker_b = make(), make()
    client = _make_client()
    asyncio.run(worker_a.register_client(client))

    code_value = _issue_code(worker_a, client)
    code_obj = asyncio.run(worker_a.load_authorization_code(client, code_value))
    token = asyncio.run(worker_a.exchange_authorization_code(client, code_obj))

    # This is the production failure: /mcp/ request lands on the other worker -> 401.
    at = asyncio.run(worker_b.verify_token(token.access_token))
    assert at is not None, "access token issued on worker A must verify on worker B"
    assert at.client_id == "client-a"
    db.close()


def test_refresh_token_rotation_works_across_workers(monkeypatch, tmp_path):
    import asyncio

    db, _repo, make = _make_split_brain_pair(tmp_path, monkeypatch)
    worker_a, worker_b = make(), make()
    client = _make_client()
    asyncio.run(worker_a.register_client(client))

    code_value = _issue_code(worker_a, client)
    code_obj = asyncio.run(worker_a.load_authorization_code(client, code_value))
    token = asyncio.run(worker_a.exchange_authorization_code(client, code_obj))

    rt_obj = asyncio.run(worker_b.load_refresh_token(client, token.refresh_token))
    assert rt_obj is not None, "refresh token issued on worker A must load on worker B"

    new_token = asyncio.run(worker_b.exchange_refresh_token(client, rt_obj, ["read"]))
    assert new_token.access_token != token.access_token

    # Rotation on B must invalidate the old pair EVERYWHERE, including worker A.
    assert asyncio.run(worker_a.verify_token(token.access_token)) is None
    assert asyncio.run(worker_a.load_refresh_token(client, token.refresh_token)) is None
    # New access token valid on both workers.
    assert asyncio.run(worker_a.verify_token(new_token.access_token)) is not None
    assert asyncio.run(worker_b.verify_token(new_token.access_token)) is not None
    db.close()


def test_pending_authorization_visible_on_other_worker(monkeypatch, tmp_path):
    import asyncio

    db, _repo, make = _make_split_brain_pair(tmp_path, monkeypatch)
    worker_a, worker_b = make(), make()
    client = _make_client()
    asyncio.run(worker_a.register_client(client))

    login_url = asyncio.run(worker_a.authorize(client, _auth_params()))
    pending_id = login_url.split("pending=")[1]

    # /api/pending and /api/consent may land on the other worker.
    pending_client = worker_b.get_pending_client(pending_id)
    assert pending_client is not None, "pending auth started on worker A must be visible on B"
    assert pending_client.client_id == "client-a"

    redirect = asyncio.run(worker_b.complete_authorization(pending_id))
    assert "code=" in redirect
    db.close()


def test_tokens_survive_restart(monkeypatch, tmp_path):
    import asyncio

    db, _repo, make = _make_split_brain_pair(tmp_path, monkeypatch)
    worker_a = make()
    client = _make_client()
    asyncio.run(worker_a.register_client(client))

    code_value = _issue_code(worker_a, client)
    code_obj = asyncio.run(worker_a.load_authorization_code(client, code_value))
    token = asyncio.run(worker_a.exchange_authorization_code(client, code_obj))

    # Coolify redeploy: brand-new process, empty memory, same DB volume.
    restarted = make()
    assert asyncio.run(restarted.verify_token(token.access_token)) is not None
    assert asyncio.run(restarted.get_client("client-a")) is not None
    rt = asyncio.run(restarted.load_refresh_token(client, token.refresh_token))
    assert rt is not None
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

    client = OAuthClientInformationFull(
        client_id="client1",
        redirect_uris=[AnyUrl("http://localhost/callback")],
    )
    old_refresh_obj = asyncio.run(provider.load_refresh_token(client, old_rt))
    assert old_refresh_obj is not None
    assert asyncio.run(provider.verify_token(old_at)) is not None

    asyncio.run(provider.exchange_refresh_token(client, old_refresh_obj, ["read"]))

    valid_ats = {r["token"] for r in repo.get_valid_access_tokens()}
    valid_rts = {r["token"] for r in repo.get_valid_refresh_tokens()}
    assert old_at not in valid_ats, "stary AT musi być usunięty z DB po rotacji"
    assert old_rt not in valid_rts, "stary RT musi być usunięty z DB po rotacji"

    # Drugi provider symuluje restart — stare tokeny nie mogą być honorowane
    provider2 = KajetOAuthProvider(
        oauth_repo=repo,
        base_url="http://localhost:8000/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )
    assert asyncio.run(provider2.verify_token(old_at)) is None
    assert asyncio.run(provider2.load_refresh_token(client, old_rt)) is None
    db.close()
