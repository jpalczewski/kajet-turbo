"""Identity resolution against real repositories.

Fakes cannot catch what matters here — that the session expiry predicate lives in SQL
while the access-token one lives in Python, and that the two must agree with auth.
"""

import time

import pytest
from sqlmodel import Session

from kajet_turbo.db import Database
from kajet_turbo.identity import (
    SESSION_COOKIE,
    bearer_token_from_headers,
    resolve_bearer_user_id,
    resolve_session_user,
    session_token_from_cookies,
    token_expired,
)
from kajet_turbo.models import UserSession
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.sessions import SessionRepository
from tests.services.conftest import seed_user


class ExplodingRepo:
    """Any DB access is a failure: an empty credential must never reach the database."""

    def __getattr__(self, name):
        raise AssertionError(f"{name} must not be called for an empty credential")


def _session(database: Database, token: str, user_id: str, *, ttl_s: int) -> None:
    with Session(database.engine) as session:
        session.add(UserSession(token=token, user_id=user_id, expires_at=int(time.time()) + ttl_s))
        session.commit()


# --- token extraction -------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("BEARER abc", "abc"),
        ("Bearer   abc  ", "abc"),
        ("Bearer ", ""),
        ("Basic abc", ""),
        ("abc", ""),
        ("", ""),
    ],
)
def test_bearer_token_from_headers(header: str, expected: str):
    assert bearer_token_from_headers({"authorization": header}) == expected


def test_bearer_token_from_headers_without_the_header():
    assert bearer_token_from_headers({}) == ""


def test_session_token_from_cookies():
    assert session_token_from_cookies({SESSION_COOKIE: "tok"}) == "tok"
    assert session_token_from_cookies({}) == ""


# --- session -----------------------------------------------------------------


def test_resolve_session_user_returns_id_and_email(database: Database):
    seed_user(database, "u1")
    _session(database, "good-token", "u1", ttl_s=3600)

    user = resolve_session_user(SessionRepository(database.engine), "good-token")

    assert user == {
        "id": "u1",
        "email": "u1@test.com",
        "timezone": "Europe/Warsaw",
        "locale": "pl",
    }


def test_resolve_session_user_refuses_an_expired_session(database: Database):
    seed_user(database, "u1")
    _session(database, "stale-token", "u1", ttl_s=-10)

    assert resolve_session_user(SessionRepository(database.engine), "stale-token") is None


def test_resolve_session_user_refuses_an_unknown_token(database: Database):
    assert resolve_session_user(SessionRepository(database.engine), "nope") is None


def test_resolve_session_user_skips_the_database_for_an_empty_token():
    assert resolve_session_user(ExplodingRepo(), "") is None  # ty: ignore[invalid-argument-type]


# --- access token expiry ------------------------------------------------------


@pytest.mark.parametrize(
    ("expires_at", "expired"),
    [(None, False), (500.0, True), (1500.0, False), (1000.0, False)],
)
def test_token_expired(expires_at: float | None, expired: bool):
    # expires_at == now is not expired: the comparison is strict, as in auth.py.
    assert token_expired({"expires_at": expires_at}, now=1000.0) is expired


# --- bearer -------------------------------------------------------------------


def _client_with_token(
    database: Database, user_id: str, client_id: str, token: str, *, ttl_s: int = 3600
) -> OAuthRepository:
    seed_user(database, user_id)
    repo = OAuthRepository(database.engine)
    repo.record_client_authorization(client_id, user_id)
    repo.upsert_access_token(
        token, client_id, ["read"], int(time.time()) + ttl_s, None, user_id=user_id
    )
    return repo


def test_resolve_bearer_user_id_returns_the_token_owner(database: Database):
    repo = _client_with_token(database, "u1", "client-1", "at-1")

    assert resolve_bearer_user_id(repo, "at-1") == "u1"


def test_a_second_users_consent_does_not_repoint_an_existing_token(database: Database):
    """The takeover this module exists to prevent.

    Identity used to be "the last user who authorized this client_id". An attacker
    holding their own token for a client only had to get a victim to approve that same
    client — one consent click — and the attacker's already-issued token started
    resolving to the victim. Tokens carry their owner now, so consent by anyone else
    must leave them alone.
    """
    repo = _client_with_token(database, "attacker", "client-1", "at-attacker")
    seed_user(database, "victim")

    repo.record_client_authorization("client-1", "victim")

    assert resolve_bearer_user_id(repo, "at-attacker") == "attacker"


def test_resolve_bearer_user_id_refuses_an_expired_token(database: Database):
    repo = _client_with_token(database, "u1", "client-1", "at-expired", ttl_s=-10)

    assert resolve_bearer_user_id(repo, "at-expired") is None


def test_resolve_bearer_user_id_leaves_the_expired_row_alone(database: Database):
    """Unlike load_access_token, the read-only path must not delete anything — a log
    lookup racing a refresh must not be what revokes the token."""
    repo = _client_with_token(database, "u1", "client-1", "at-expired", ttl_s=-10)

    resolve_bearer_user_id(repo, "at-expired")

    assert repo.get_access_token("at-expired") is not None


def test_resolve_bearer_user_id_refuses_an_unknown_token(database: Database):
    repo = _client_with_token(database, "u1", "client-1", "at-1")

    assert resolve_bearer_user_id(repo, "never-issued") is None


def test_a_token_predating_the_user_id_column_authenticates_nobody(database: Database):
    """Backfill leaves user_id NULL when the client had no recorded consent. An
    unattributable credential must fail closed, not fall back to a client lookup."""
    seed_user(database, "u1")
    repo = OAuthRepository(database.engine)
    repo.record_client_authorization("client-1", "u1")
    repo.upsert_access_token("at-legacy", "client-1", ["read"], int(time.time()) + 3600, None)

    assert resolve_bearer_user_id(repo, "at-legacy") is None


def test_resolve_bearer_user_id_skips_the_database_for_an_empty_token():
    assert resolve_bearer_user_id(ExplodingRepo(), "") is None  # ty: ignore[invalid-argument-type]
