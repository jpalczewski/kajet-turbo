import pytest
from pydantic import AnyUrl

from kajet_turbo.auth import KajetOAuthProvider


def test_expired_access_token_preserves_refresh_token(monkeypatch, database):
    """AT expiry must NOT wipe the paired RT — client needs RT to refresh."""
    import asyncio
    import time

    from mcp.server.auth.settings import ClientRegistrationOptions

    from kajet_turbo.auth import KajetOAuthProvider
    from kajet_turbo.repositories.oauth import OAuthRepository
    from tests.services.conftest import seed_user

    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    # Consent and issued tokens both reference users.id, so the flow needs a real user.
    seed_user(database, "u1")
    repo = OAuthRepository(database.engine)
    now = int(time.time())

    provider = KajetOAuthProvider(
        oauth_repo=repo,
        base_url="http://localhost:8000/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )

    # Expired AT paired with a valid RT (simulates in-session expiry).
    rt_val = "rt_valid_xyz"
    at_val = "at_expired_xyz"
    repo.upsert_refresh_token(rt_val, "client1", ["read"], None, user_id="u1")
    repo.upsert_access_token(at_val, "client1", ["read"], now - 10, rt_val, user_id="u1")

    result = asyncio.run(provider.verify_token(at_val))
    assert result is None

    client = _make_client("client1")
    rt = asyncio.run(provider.load_refresh_token(client, rt_val))
    assert rt is not None, "RT must survive AT expiry so client can refresh"


# --- Split-brain tests: two provider instances sharing one DB simulate ---
# --- two uvicorn workers behind a round-robin proxy (MCP_WORKERS>1).    ---


def _make_split_brain_pair(database, monkeypatch):
    from mcp.server.auth.settings import ClientRegistrationOptions

    from kajet_turbo.repositories.oauth import OAuthRepository
    from tests.services.conftest import seed_user

    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    # Consent and issued tokens both reference users.id, so the flow needs a real user.
    seed_user(database, "u1")
    repo = OAuthRepository(database.engine)

    def make():
        return KajetOAuthProvider(
            oauth_repo=repo,
            base_url="http://localhost:8000/mcp",
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )

    return repo, make


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


def _issue_code(provider, client, user_id="u1"):
    """Run authorize + login-completion on one provider, return the auth code value.

    A user is required: an authorization code with no owner cannot be exchanged, since
    the resulting token would have nobody to belong to."""
    import asyncio
    from urllib.parse import parse_qs, urlparse

    login_url = asyncio.run(provider.authorize(client, _auth_params()))
    pending_id = login_url.split("pending=")[1]
    redirect = asyncio.run(provider.complete_authorization(pending_id, user_id))
    return parse_qs(urlparse(redirect).query)["code"][0]


def test_client_registered_on_one_worker_visible_on_another(monkeypatch, database):
    import asyncio

    _repo, make = _make_split_brain_pair(database, monkeypatch)
    worker_a, worker_b = make(), make()

    asyncio.run(worker_a.register_client(_make_client()))

    found = asyncio.run(worker_b.get_client("client-a"))
    assert found is not None, "client registered on worker A must be visible on worker B"
    assert found.client_id == "client-a"


def test_auth_code_created_on_one_worker_exchangeable_on_another(monkeypatch, database):
    import asyncio

    _repo, make = _make_split_brain_pair(database, monkeypatch)
    worker_a, worker_b = make(), make()
    client = _make_client()
    asyncio.run(worker_a.register_client(client))

    code_value = _issue_code(worker_a, client)

    code_obj = asyncio.run(worker_b.load_authorization_code(client, code_value))
    assert code_obj is not None, "auth code created on worker A must load on worker B"

    token = asyncio.run(worker_b.exchange_authorization_code(client, code_obj))
    assert token.access_token
    assert token.refresh_token


def test_auth_code_is_single_use_across_workers(monkeypatch, database):
    import asyncio

    from mcp.server.auth.provider import TokenError

    _repo, make = _make_split_brain_pair(database, monkeypatch)
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


def test_access_token_issued_on_one_worker_valid_on_another(monkeypatch, database):
    import asyncio

    _repo, make = _make_split_brain_pair(database, monkeypatch)
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


def test_refresh_token_rotation_works_across_workers(monkeypatch, database):
    import asyncio

    _repo, make = _make_split_brain_pair(database, monkeypatch)
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
    # New access token valid on both workers.
    assert asyncio.run(worker_a.verify_token(new_token.access_token)) is not None
    assert asyncio.run(worker_b.verify_token(new_token.access_token)) is not None

    # Presenting the consumed RT is replay: it revokes the active descendant family.
    assert asyncio.run(worker_a.load_refresh_token(client, token.refresh_token)) is None
    assert asyncio.run(worker_a.verify_token(new_token.access_token)) is None


def test_pending_authorization_visible_on_other_worker(monkeypatch, database):
    import asyncio

    _repo, make = _make_split_brain_pair(database, monkeypatch)
    worker_a, worker_b = make(), make()
    client = _make_client()
    asyncio.run(worker_a.register_client(client))

    login_url = asyncio.run(worker_a.authorize(client, _auth_params()))
    pending_id = login_url.split("pending=")[1]

    # /api/pending and /api/consent may land on the other worker.
    pending_client = worker_b.get_pending_client(pending_id)
    assert pending_client is not None, "pending auth started on worker A must be visible on B"
    assert pending_client.client_id == "client-a"

    redirect = asyncio.run(worker_b.complete_authorization(pending_id, "u1"))
    assert "code=" in redirect


def test_tokens_survive_restart(monkeypatch, database):
    import asyncio

    _repo, make = _make_split_brain_pair(database, monkeypatch)
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


def test_exchange_refresh_token_deletes_old_tokens_from_db(monkeypatch, database):
    import asyncio
    import time

    from mcp.server.auth.settings import ClientRegistrationOptions
    from mcp.shared.auth import OAuthClientInformationFull

    from kajet_turbo.auth import KajetOAuthProvider
    from kajet_turbo.repositories.oauth import OAuthRepository
    from tests.services.conftest import seed_user

    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    # Consent and issued tokens both reference users.id, so the flow needs a real user.
    seed_user(database, "u1")
    repo = OAuthRepository(database.engine)

    now = int(time.time())
    old_rt = "old_refresh_token_xyz"
    old_at = "old_access_token_xyz"
    repo.upsert_refresh_token(old_rt, "client1", ["read"], None, user_id="u1")
    repo.upsert_access_token(old_at, "client1", ["read"], now + 3600, old_rt, user_id="u1")

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


def test_access_token_without_an_owner_is_rejected(database, monkeypatch):
    """A token predating the user_id column cannot be attributed, so it must fail at the
    bearer layer (401) rather than later in the tool call. Only a 401 makes an MCP client
    re-run OAuth; a tool error it just retries."""
    import asyncio
    import time

    from mcp.server.auth.settings import ClientRegistrationOptions

    from kajet_turbo.repositories.oauth import OAuthRepository
    from tests.services.conftest import seed_user

    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    seed_user(database, "u1")
    repo = OAuthRepository(database.engine)
    repo.upsert_access_token("at-no-owner", "client1", ["read"], int(time.time()) + 3600)

    provider = KajetOAuthProvider(
        oauth_repo=repo,
        base_url="http://localhost:8000/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )

    assert asyncio.run(provider.verify_token("at-no-owner")) is None


def test_refresh_token_without_an_owner_is_rejected(database, monkeypatch):
    import asyncio

    from mcp.server.auth.settings import ClientRegistrationOptions

    from kajet_turbo.repositories.oauth import OAuthRepository

    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    repo = OAuthRepository(database.engine)
    repo.upsert_refresh_token("rt-no-owner", "client1", ["read"], None)

    provider = KajetOAuthProvider(
        oauth_repo=repo,
        base_url="http://localhost:8000/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )

    assert asyncio.run(provider.load_refresh_token(_make_client("client1"), "rt-no-owner")) is None


def test_access_token_subject_is_its_owner_for_shared_client(database, monkeypatch):
    import asyncio
    import time

    from kajet_turbo.repositories.oauth import OAuthRepository
    from tests.services.conftest import seed_user

    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    seed_user(database, "user-a")
    seed_user(database, "user-b")
    repo = OAuthRepository(database.engine)
    expires = int(time.time()) + 3600
    repo.upsert_access_token("at-a", "shared-client", [], expires, user_id="user-a")
    repo.upsert_access_token("at-b", "shared-client", [], expires, user_id="user-b")
    provider = KajetOAuthProvider(repo, base_url="http://localhost:8000/mcp")

    token_a = asyncio.run(provider.load_access_token("at-a"))
    token_b = asyncio.run(provider.load_access_token("at-b"))

    assert token_a is not None and token_a.subject == "user-a"
    assert token_b is not None and token_b.subject == "user-b"
    assert token_a.client_id == token_b.client_id == "shared-client"


def test_refresh_token_has_absolute_family_expiry(database, monkeypatch):
    import asyncio

    from kajet_turbo.auth import REFRESH_TOKEN_EXPIRY_SECONDS

    _repo, make = _make_split_brain_pair(database, monkeypatch)
    provider = make()
    now = 2_000_000_000
    monkeypatch.setattr("kajet_turbo.auth.time.time", lambda: now)

    token = asyncio.run(provider._issue_token_pair("client-a", ["read"], "u1"))
    refresh = asyncio.run(provider.load_refresh_token(_make_client(), token.refresh_token))
    assert refresh is not None
    assert refresh.expires_at == now + REFRESH_TOKEN_EXPIRY_SECONDS

    monkeypatch.setattr("kajet_turbo.auth.time.time", lambda: now + 3600)
    rotated = asyncio.run(provider.exchange_refresh_token(_make_client(), refresh, ["read"]))
    successor = asyncio.run(provider.load_refresh_token(_make_client(), rotated.refresh_token))
    assert successor is not None
    assert successor.expires_at == now + REFRESH_TOKEN_EXPIRY_SECONDS


def test_revocation_enabled_and_revokes_token_family(database, monkeypatch):
    import asyncio

    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from kajet_turbo.auth import create_auth

    repo, _make = _make_split_brain_pair(database, monkeypatch)
    provider = create_auth(repo)
    client_info = _make_client()
    client_info.token_endpoint_auth_method = "none"
    asyncio.run(provider.register_client(client_info))
    token = asyncio.run(provider._issue_token_pair("client-a", ["read"], "u1"))
    access = asyncio.run(provider.load_access_token(token.access_token))
    assert access is not None

    assert provider.revocation_options is not None
    assert provider.revocation_options.enabled is True
    routes = provider.get_routes(mcp_path="/")
    assert "/revoke" in {route.path for route in routes}
    app_client = TestClient(Starlette(routes=routes))
    metadata = app_client.get("/.well-known/oauth-authorization-server")
    assert metadata.status_code == 200
    assert metadata.json()["revocation_endpoint"].endswith("/mcp/revoke")

    revoked = app_client.post(
        "/revoke",
        data={"token": token.access_token, "client_id": "client-a", "client_secret": ""},
    )
    assert revoked.status_code == 200
    assert asyncio.run(provider.load_access_token(token.access_token)) is None
    assert token.refresh_token is not None
    assert asyncio.run(provider.load_refresh_token(_make_client(), token.refresh_token)) is None


def test_concurrent_refresh_replay_revokes_winners_descendant(database, monkeypatch):
    import asyncio

    from mcp.server.auth.provider import TokenError

    _repo, make = _make_split_brain_pair(database, monkeypatch)
    worker_a, worker_b = make(), make()
    client = _make_client()
    token = asyncio.run(worker_a._issue_token_pair("client-a", ["read"], "u1"))
    refresh_a = asyncio.run(worker_a.load_refresh_token(client, token.refresh_token))
    refresh_b = asyncio.run(worker_b.load_refresh_token(client, token.refresh_token))
    assert refresh_a is not None and refresh_b is not None

    async def race():
        return await asyncio.gather(
            worker_a.exchange_refresh_token(client, refresh_a, ["read"]),
            worker_b.exchange_refresh_token(client, refresh_b, ["read"]),
            return_exceptions=True,
        )

    results = asyncio.run(race())
    winners = [result for result in results if not isinstance(result, BaseException)]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(winners) == 1
    assert len(failures) == 1 and isinstance(failures[0], TokenError)
    assert asyncio.run(worker_a.load_access_token(winners[0].access_token)) is None


def test_replaying_an_expired_consumed_token_still_logs_reuse(database, monkeypatch, capsys):
    """A consumed RT that is also past its absolute expiry must take the reuse branch,
    not the plain-expiry one, or oauth_refresh_reuse_detected never fires."""
    import asyncio
    import time

    from kajet_turbo.log import setup_logging
    from tests.helpers import entries_named, read_log_entries

    _repo, make = _make_split_brain_pair(database, monkeypatch)
    provider = make()
    now = int(time.time())
    _repo.upsert_refresh_token(
        "rt-old",
        "client-a",
        ["read"],
        now - 10,
        user_id="u1",
        family_id="fam-1",
        consumed_at=now - 20,
    )
    _repo.upsert_access_token("at-child", "client-a", ["read"], now + 3600, "rt-child", "u1")
    _repo.upsert_refresh_token(
        "rt-child", "client-a", ["read"], now + 3600, user_id="u1", family_id="fam-1"
    )
    setup_logging()  # after seeding, so only the load_refresh_token line is captured

    result = asyncio.run(provider.load_refresh_token(_make_client(), "rt-old"))

    assert result is None
    reuse_warnings = entries_named(read_log_entries(capsys), "oauth_refresh_reuse_detected")
    assert len(reuse_warnings) == 1
    assert reuse_warnings[0]["family_id"] == "fam-1"
    assert asyncio.run(provider.load_access_token("at-child")) is None
