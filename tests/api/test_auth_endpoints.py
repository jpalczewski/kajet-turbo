import time

from starlette.testclient import TestClient

from kajet_turbo.api.auth import router
from kajet_turbo.auth import DUMMY_PASSWORD_HASH, hash_password
from kajet_turbo.dependencies import (
    CurrentUser,
    get_oauth_repo,
    get_provider,
    get_required_user,
    get_session_repo,
    get_user_repo,
)
from kajet_turbo.errors import SecurityEvent, SecurityReason
from kajet_turbo.log import LoggingMiddleware, setup_logging
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.sessions import SessionRepository
from kajet_turbo.repositories.users import UserRepository
from tests.api.conftest import build_test_app
from tests.helpers import entries_named, read_log_entries


def _client(database, *, user_id: str | None = None):
    users = UserRepository(database.engine)
    sessions = SessionRepository(database.engine)
    oauth = OAuthRepository(database.engine)
    app = build_test_app(routers=(router,))
    app.dependency_overrides[get_user_repo] = lambda: users
    app.dependency_overrides[get_session_repo] = lambda: sessions
    app.dependency_overrides[get_oauth_repo] = lambda: oauth
    app.dependency_overrides[get_provider] = object
    if user_id is not None:
        app.dependency_overrides[get_required_user] = lambda: CurrentUser(
            id=user_id, email="u@test", timezone="", locale=""
        )
    return TestClient(app), users, sessions, oauth


def test_session_get_includes_preferences(database):
    client, users, _sessions, _oauth = _client(database)
    user_id = users.create("pref@example.com", hash_password("password"))
    client.app.dependency_overrides[get_required_user] = lambda: CurrentUser(
        id=user_id, email="pref@example.com", timezone="Europe/Warsaw", locale="pl"
    )

    response = client.get("/api/session")

    assert response.status_code == 200
    assert response.json() == {
        "email": "pref@example.com",
        "preferences": {"timezone": "Europe/Warsaw", "locale": "pl"},
    }


def test_unknown_email_still_runs_password_verification_once(database, monkeypatch):
    client, _users, _sessions, _oauth = _client(database)
    calls = []

    def verify(password_hash: str, password: str) -> bool:
        calls.append((password_hash, password))
        return False

    monkeypatch.setattr("kajet_turbo.api.auth.verify_password", verify)

    response = client.post("/api/login", json={"email": "missing@example.com", "password": "x"})

    assert response.status_code == 401
    assert response.json() == {"error": "Nieprawidłowy email lub hasło."}
    assert calls == [(DUMMY_PASSWORD_HASH, "x")]


def test_password_login_outcomes_are_audited_without_exposing_credentials(database, capsys):
    client, users, _sessions, _oauth = _client(database)
    user_id = users.create("known@example.com", hash_password("correct password"))
    setup_logging()

    unknown = client.post(
        "/api/login", json={"email": "missing@example.com", "password": "wrong password"}
    )
    wrong = client.post(
        "/api/login", json={"email": "known@example.com", "password": "wrong password"}
    )
    success = client.post(
        "/api/login", json={"email": "known@example.com", "password": "correct password"}
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.content == wrong.content
    assert success.status_code == 200
    entries = read_log_entries(capsys)
    events = entries_named(entries, SecurityEvent.AUTH_FAILURE.value)
    assert [(event["reason"], event["user_id"]) for event in events] == [
        (SecurityReason.UNKNOWN_EMAIL.value, None),
        (SecurityReason.BAD_CREDENTIALS.value, user_id),
    ]
    (success_event,) = entries_named(entries, SecurityEvent.AUTH_SUCCESS.value)
    assert success_event["auth_method"] == "password"
    assert success_event["user_id"] == user_id
    serialized = str([*events, success_event])
    for secret in (
        "missing@example.com",
        "known@example.com",
        "wrong password",
        "correct password",
    ):
        assert secret not in serialized


def test_expired_pending_login_is_an_audited_failure(database, capsys):
    client, users, _sessions, _oauth = _client(database)
    user_id = users.create("known@example.com", hash_password("correct password"))

    class ExpiredProvider:
        async def complete_authorization(self, pending_id: str, owner_id: str) -> str:
            raise ValueError("expired")

    client.app.dependency_overrides[get_provider] = ExpiredProvider
    setup_logging()

    response = client.post(
        "/api/login",
        json={"email": "known@example.com", "password": "correct password", "pending_id": "gone"},
    )

    assert response.status_code == 400
    (event,) = entries_named(read_log_entries(capsys), SecurityEvent.AUTH_FAILURE.value)
    assert event["reason"] == SecurityReason.EXPIRED_PENDING.value
    assert event["user_id"] == user_id
    assert event["auth_method"] == "password"


def test_failed_login_does_not_mark_its_http_record_as_security(database, capsys):
    users = UserRepository(database.engine)
    sessions = SessionRepository(database.engine)
    app = build_test_app(routers=(router,))
    app.add_middleware(LoggingMiddleware)
    app.dependency_overrides[get_user_repo] = lambda: users
    app.dependency_overrides[get_session_repo] = lambda: sessions
    app.dependency_overrides[get_provider] = object
    setup_logging()

    with TestClient(app) as client:
        response = client.post(
            "/api/login", json={"email": "missing@example.com", "password": "wrong"}
        )

    assert response.status_code == 401
    entries = read_log_entries(capsys)
    (security_event,) = entries_named(entries, SecurityEvent.AUTH_FAILURE.value)
    assert security_event["category"] == "security"
    (http_event,) = entries_named(entries, "http")
    assert "category" not in http_event


def test_logout_everywhere_deletes_only_current_users_credentials(database):
    client, users, sessions, oauth = _client(database)
    user_1 = users.create("one@example.com", hash_password("password"))
    user_2 = users.create("two@example.com", hash_password("password"))
    client.app.dependency_overrides[get_required_user] = lambda: CurrentUser(
        id=user_1, email="one@example.com", timezone="", locale=""
    )
    session_1a = sessions.create(user_1)
    session_1b = sessions.create(user_1)
    session_2 = sessions.create(user_2)
    expires = int(time.time()) + 3600
    oauth.upsert_refresh_token("rt-u1", "client", [], expires, user_id=user_1)
    oauth.upsert_access_token("at-u1", "client", [], expires, "rt-u1", user_id=user_1)
    oauth.upsert_refresh_token("rt-u2", "client", [], expires, user_id=user_2)
    oauth.upsert_access_token("at-u2", "client", [], expires, "rt-u2", user_id=user_2)
    oauth.upsert_auth_code(
        "code-u1", "client", user_1, "http://localhost/callback", True, [], expires, None
    )

    response = client.delete("/api/sessions")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert sessions.get_user(session_1a) is None
    assert sessions.get_user(session_1b) is None
    assert oauth.get_access_token("at-u1") is None
    assert oauth.get_refresh_token("rt-u1") is None
    assert oauth.get_auth_code("code-u1") is None
    assert sessions.get_user(session_2) is not None
    assert oauth.get_access_token("at-u2") is not None


def test_local_logout_preserves_other_sessions_and_oauth(database):
    client, users, sessions, oauth = _client(database)
    user_id = users.create("one@example.com", hash_password("password"))
    current = sessions.create(user_id)
    other = sessions.create(user_id)
    expires = int(time.time()) + 3600
    oauth.upsert_access_token("at-u1", "client", [], expires, user_id=user_id)
    client.cookies.set("kajet_session", current)

    response = client.delete("/api/session")

    assert response.status_code == 200
    assert sessions.get_user(current) is None
    assert sessions.get_user(other) is not None
    assert oauth.get_access_token("at-u1") is not None
