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
    user_id_for_client,
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

    assert user == {"id": "u1", "email": "u1@test.com"}


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


def _authorized_client(database: Database, user_id: str, client_id: str) -> OAuthRepository:
    seed_user(database, user_id)
    repo = OAuthRepository(database.engine)
    repo.record_client_authorization(client_id, user_id)
    return repo


def test_resolve_bearer_user_id_returns_the_authorizing_user(database: Database):
    repo = _authorized_client(database, "u1", "client-1")
    repo.upsert_access_token("at-1", "client-1", ["read"], int(time.time()) + 3600, None)

    assert resolve_bearer_user_id(repo, "at-1") == "u1"


def test_resolve_bearer_user_id_refuses_an_expired_token(database: Database):
    """The regression this module exists for: the log path used to skip this check and
    then cache the answer, so logs credited a user whose token auth had just rejected."""
    repo = _authorized_client(database, "u1", "client-1")
    repo.upsert_access_token("at-expired", "client-1", ["read"], int(time.time()) - 10, None)

    assert resolve_bearer_user_id(repo, "at-expired") is None


def test_resolve_bearer_user_id_leaves_the_expired_row_alone(database: Database):
    """Unlike load_access_token, the read-only path must not delete anything — a log
    lookup racing a refresh must not be what revokes the token."""
    repo = _authorized_client(database, "u1", "client-1")
    repo.upsert_access_token("at-expired", "client-1", ["read"], int(time.time()) - 10, None)

    resolve_bearer_user_id(repo, "at-expired")

    assert repo.get_access_token("at-expired") is not None


def test_resolve_bearer_user_id_refuses_an_unknown_token(database: Database):
    repo = _authorized_client(database, "u1", "client-1")

    assert resolve_bearer_user_id(repo, "never-issued") is None


def test_resolve_bearer_user_id_refuses_a_client_nobody_authorized(database: Database):
    repo = OAuthRepository(database.engine)
    repo.upsert_access_token("at-1", "orphan-client", ["read"], int(time.time()) + 3600, None)

    assert resolve_bearer_user_id(repo, "at-1") is None


def test_resolve_bearer_user_id_skips_the_database_for_an_empty_token():
    assert resolve_bearer_user_id(ExplodingRepo(), "") is None  # ty: ignore[invalid-argument-type]


def test_user_id_for_client_is_the_last_authorizer(database: Database):
    """client_authorizations is INSERT OR REPLACE on client_id alone, so re-authorizing
    the same client as a different user moves the whole client over."""
    seed_user(database, "u1")
    seed_user(database, "u2")
    repo = OAuthRepository(database.engine)
    repo.record_client_authorization("client-1", "u1")
    repo.record_client_authorization("client-1", "u2")

    assert user_id_for_client(repo, "client-1") == "u2"
