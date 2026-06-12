import time
from datetime import UTC, datetime

from kajet_turbo.db import Database
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.users import UserRepository


def test_oauth_repository(database: Database):
    users = UserRepository(database.engine)
    oauth = OAuthRepository(database.engine)
    user_id = users.create("o@p.com", "hash")

    oauth.upsert_registered_client("cl1", '{"client_id":"cl1"}')
    assert oauth.get_all_registered_clients() == ['{"client_id":"cl1"}']

    oauth.record_client_authorization("cl1", user_id)
    assert oauth.get_user_id_by_client("cl1") == user_id

    oauth.upsert_access_token("tok1", "cl1", ["read"], None)
    assert any(token["token"] == "tok1" for token in oauth.get_valid_access_tokens())

    oauth.upsert_refresh_token("ref1", "cl1", ["read"], None)
    assert any(token["token"] == "ref1" for token in oauth.get_valid_refresh_tokens())

    oauth.save_oauth_client("cl2", "secret", ["https://cb.local"], "2026-01-01T00:00:00")
    client = oauth.get_oauth_client("cl2")
    assert client is not None
    assert client["client_secret"] == "secret"


def test_save_and_get_oauth_client(database: Database):
    oauth = OAuthRepository(database.engine)

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


def test_get_oauth_client_returns_none_for_missing(database: Database):
    oauth = OAuthRepository(database.engine)

    assert oauth.get_oauth_client("missing") is None


def test_delete_expired_tokens_leaves_valid_and_null_expiry(database: Database):
    repository = OAuthRepository(database.engine)
    now = int(time.time())
    repository.upsert_access_token("at-expired", "c1", [], now - 10)
    repository.upsert_access_token("at-valid", "c1", [], now + 3600)
    repository.upsert_refresh_token("rt-expired", "c1", [], now - 10)
    repository.upsert_refresh_token("rt-null", "c1", [], None)

    repository.delete_expired_tokens()

    valid_access_tokens = {row["token"] for row in repository.get_valid_access_tokens()}
    valid_refresh_tokens = {row["token"] for row in repository.get_valid_refresh_tokens()}
    assert valid_access_tokens == {"at-valid"}
    assert valid_refresh_tokens == {"rt-null"}


def test_delete_individual_tokens(database: Database):
    repository = OAuthRepository(database.engine)
    now = int(time.time())
    repository.upsert_access_token("at1", "c1", [], now + 3600)
    repository.upsert_refresh_token("rt1", "c1", [], None)

    repository.delete_access_token("at1")
    repository.delete_refresh_token("rt1")

    assert repository.get_valid_access_tokens() == []
    assert repository.get_valid_refresh_tokens() == []
