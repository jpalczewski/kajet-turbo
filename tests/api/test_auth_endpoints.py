import time

from fastapi import FastAPI
from starlette.testclient import TestClient

from kajet_turbo.api.auth import router
from kajet_turbo.auth import DUMMY_PASSWORD_HASH, hash_password
from kajet_turbo.dependencies import (
    get_oauth_repo,
    get_provider,
    get_required_user,
    get_session_repo,
    get_user_repo,
)
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.sessions import SessionRepository
from kajet_turbo.repositories.users import UserRepository


def _client(database, *, user_id: str | None = None):
    users = UserRepository(database.engine)
    sessions = SessionRepository(database.engine)
    oauth = OAuthRepository(database.engine)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user_repo] = lambda: users
    app.dependency_overrides[get_session_repo] = lambda: sessions
    app.dependency_overrides[get_oauth_repo] = lambda: oauth
    app.dependency_overrides[get_provider] = object
    if user_id is not None:
        app.dependency_overrides[get_required_user] = lambda: {"id": user_id, "email": "u@test"}
    return TestClient(app), users, sessions, oauth


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


def test_logout_everywhere_deletes_only_current_users_credentials(database):
    client, users, sessions, oauth = _client(database)
    user_1 = users.create("one@example.com", hash_password("password"))
    user_2 = users.create("two@example.com", hash_password("password"))
    client.app.dependency_overrides[get_required_user] = lambda: {
        "id": user_1,
        "email": "one@example.com",
    }
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
