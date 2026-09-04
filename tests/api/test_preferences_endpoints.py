from fastapi import FastAPI
from sqlmodel import Session
from starlette.testclient import TestClient

from kajet_turbo.api.preferences import router
from kajet_turbo.dependencies import get_preferences_service, get_required_user
from kajet_turbo.models import User
from kajet_turbo.repositories.users import UserRepository
from kajet_turbo.services.preferences import PreferencesService


def _app(database, *, user_id="u1"):
    with Session(database.engine) as s:
        s.add(User(id=user_id, email=f"{user_id}@e.com", created_at="2026-01-01"))
        s.commit()
    svc = PreferencesService(UserRepository(database.engine))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_preferences_service] = lambda: svc
    app.dependency_overrides[get_required_user] = lambda: {"id": user_id}
    return TestClient(app)


def test_get_returns_defaults(database):
    client = _app(database)
    resp = client.get("/api/me/preferences")
    assert resp.status_code == 200
    assert resp.json() == {"timezone": "Europe/Warsaw", "locale": "pl"}


def test_patch_empty_body_is_noop(database):
    client = _app(database)
    resp = client.patch("/api/me/preferences", json={})
    assert resp.status_code == 200
    assert resp.json() == {"timezone": "Europe/Warsaw", "locale": "pl"}


def test_patch_single_field_changes_only_that_field(database):
    client = _app(database)
    resp = client.patch("/api/me/preferences", json={"timezone": "America/New_York"})
    assert resp.status_code == 200
    assert resp.json() == {"timezone": "America/New_York", "locale": "pl"}


def test_patch_invalid_case_timezone_returns_422(database):
    client = _app(database)
    # Valid ZoneInfo() construction on macOS (case-insensitive FS) but must fail
    # exact-membership validation regardless of host OS.
    resp = client.patch("/api/me/preferences", json={"timezone": "europe/warsaw"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "PREFERENCES_INVALID_INPUT"


def test_patch_unsupported_locale_returns_422(database):
    client = _app(database)
    resp = client.patch("/api/me/preferences", json={"locale": "fr"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "PREFERENCES_INVALID_INPUT"


def test_patch_explicit_null_timezone_returns_422(database):
    client = _app(database)
    resp = client.patch("/api/me/preferences", json={"timezone": None})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "PREFERENCES_INVALID_INPUT"


def test_patch_explicit_null_locale_returns_422(database):
    client = _app(database)
    resp = client.patch("/api/me/preferences", json={"locale": None})
    assert resp.status_code == 422
    assert resp.json()["detail"] == "PREFERENCES_INVALID_INPUT"
