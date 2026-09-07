from starlette.testclient import TestClient

from kajet_turbo.api.embedding import router
from kajet_turbo.crypto import cipher_for
from kajet_turbo.dependencies import CurrentUser, get_embedding_profile_service, get_required_user
from kajet_turbo.repositories.embedding_profiles import EmbeddingProfileRepository
from kajet_turbo.services.embedding_profiles import EmbeddingProfileService
from tests.api.conftest import build_test_app
from tests.conftest import seed_user


def _app(database, monkeypatch, *, user_id="u1", probe_dim=3, probe_error=None):
    if user_id:
        seed_user(database, user_id)

    def probe(base_url, model, api_key):
        if probe_error:
            raise probe_error
        return probe_dim

    svc = EmbeddingProfileService(
        EmbeddingProfileRepository(database.engine),
        cipher_factory=lambda: cipher_for("embedding", secret="server-secret"),
        probe_dim=probe,
    )
    app = build_test_app(routers=(router,))
    app.dependency_overrides[get_embedding_profile_service] = lambda: svc
    if user_id:
        app.dependency_overrides[get_required_user] = lambda: CurrentUser(
            id=user_id, email="", timezone="", locale=""
        )
    return TestClient(app), svc


def test_create_with_asyncio_probe_offloads_to_thread(database, monkeypatch):
    # Regression: the real probe runs asyncio.run(); the create route is async, so calling
    # the service inline would hit "asyncio.run() cannot be called from a running event loop".
    # The route must offload via run_sync. A probe that itself uses asyncio.run reproduces it.
    import asyncio

    seed_user(database, "u1")

    def asyncio_probe(base_url, model, api_key):
        async def _run() -> int:
            return 7

        return asyncio.run(_run())

    svc = EmbeddingProfileService(
        EmbeddingProfileRepository(database.engine),
        cipher_factory=lambda: cipher_for("embedding", secret="server-secret"),
        probe_dim=asyncio_probe,
    )
    app = build_test_app(routers=(router,))
    app.dependency_overrides[get_embedding_profile_service] = lambda: svc
    app.dependency_overrides[get_required_user] = lambda: CurrentUser(
        id="u1", email="", timezone="", locale=""
    )
    client = TestClient(app)
    r = client.post(
        "/api/me/embedding-profiles",
        json={"name": "P", "base_url": "http://h/v1", "model": "m", "api_key": "k"},
    )
    assert r.status_code == 201
    assert r.json()["dim"] == 7


def test_list_requires_auth(database, monkeypatch):
    client, _ = _app(database, monkeypatch, user_id=None)
    assert client.get("/api/me/embedding-profiles").status_code == 401


def test_create_update_activate_delete_require_auth(database, monkeypatch):
    # Per-route 401 coverage (tests/api/test_notes.py convention): each mutating route
    # needs its own check, not just the list route.
    client, _ = _app(database, monkeypatch, user_id=None)
    assert (
        client.post(
            "/api/me/embedding-profiles",
            json={"name": "A", "base_url": "http://a/v1", "model": "m"},
        ).status_code
        == 401
    )
    assert (
        client.put(
            "/api/me/embedding-profiles/nope",
            json={"name": "A", "base_url": "http://a/v1", "model": "m"},
        ).status_code
        == 401
    )
    assert client.post("/api/me/embedding-profiles/nope/activate").status_code == 401
    assert client.delete("/api/me/embedding-profiles/nope").status_code == 401


def test_list_response_matches_response_model(database, monkeypatch):
    # Regression: GET must return the typed EmbeddingProfilesResponse/EmbeddingProfileItem
    # shape exactly (was previously a raw JSONResponse bypassing response_model filtering).
    client, _ = _app(database, monkeypatch)
    client.post(
        "/api/me/embedding-profiles",
        json={"name": "A", "base_url": "http://a/v1", "model": "m", "api_key": "sk-x"},
    )
    listed = client.get("/api/me/embedding-profiles").json()
    assert set(listed.keys()) == {"profiles"}
    assert set(listed["profiles"][0].keys()) == {
        "id",
        "name",
        "base_url",
        "model",
        "dim",
        "is_active",
        "has_key",
    }


def test_create_list_activate_flow(database, monkeypatch):
    client, _ = _app(database, monkeypatch, probe_dim=1024)
    r = client.post(
        "/api/me/embedding-profiles",
        json={"name": "mmlw", "base_url": "http://h/v1", "model": "m", "api_key": "sk-x"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["dim"] == 1024 and body["is_active"] is True and body["has_key"] is True
    assert "sk-x" not in r.text and "api_key" not in body

    listing = client.get("/api/me/embedding-profiles").json()["profiles"]
    assert len(listing) == 1 and listing[0]["id"] == body["id"]


def test_create_probe_failure_is_400(database, monkeypatch):
    client, _ = _app(
        database, monkeypatch, probe_error=RuntimeError("401 from embedder: key=sk-secret-abc")
    )
    r = client.post(
        "/api/me/embedding-profiles",
        json={"name": "bad", "base_url": "http://h/v1", "model": "m", "api_key": "sk-secret-abc"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "EMBEDDING_PROFILE_PROBE_FAILED"
    # Regression for #254: the submitted api_key must never appear in an error body.
    assert "sk-secret-abc" not in r.text


def test_create_missing_required_field_is_422(database, monkeypatch):
    # Regression: a missing "base_url"/"model" must fall back to generic INVALID_INPUT --
    # CreateEmbeddingProfileRequest declares no legacy_error_codes (api/errors.py), so it
    # never had a hand-rolled code for the frontend to key off of here.
    client, _ = _app(database, monkeypatch)
    r = client.post("/api/me/embedding-profiles", json={"name": "x"})
    assert r.status_code == 422
    assert r.json()["error"] == "INVALID_INPUT"


def test_activate_success_returns_ok(database, monkeypatch):
    client, _ = _app(database, monkeypatch)
    pid = client.post(
        "/api/me/embedding-profiles",
        json={"name": "A", "base_url": "http://a/v1", "model": "m", "api_key": "k"},
    ).json()["id"]
    r = client.post(f"/api/me/embedding-profiles/{pid}/activate")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_activate_unknown_is_404(database, monkeypatch):
    client, _ = _app(database, monkeypatch)
    r = client.post("/api/me/embedding-profiles/nope/activate")
    assert r.status_code == 404
    assert r.json()["error"] == "EMBEDDING_PROFILE_NOT_FOUND"


def test_update_unknown_is_404(database, monkeypatch):
    client, _ = _app(database, monkeypatch)
    r = client.put(
        "/api/me/embedding-profiles/nope",
        json={"name": "A", "base_url": "http://a/v1", "model": "m"},
    )
    assert r.status_code == 404
    assert r.json()["error"] == "EMBEDDING_PROFILE_NOT_FOUND"


def test_update_probe_failure_is_400_not_404(database, monkeypatch):
    # Regression for #254: update_profile must distinguish "profile not found" from a
    # probe failure without string-sniffing the ValueError message -- same client/service,
    # so the only thing that changes between the two calls is the probe's behavior.
    client, svc = _app(database, monkeypatch)
    pid = client.post(
        "/api/me/embedding-profiles",
        json={"name": "A", "base_url": "http://a/v1", "model": "m", "api_key": "k"},
    ).json()["id"]

    def failing_probe(base_url, model, api_key):
        raise RuntimeError("boom")

    monkeypatch.setattr(svc, "_probe", failing_probe)
    r = client.put(
        f"/api/me/embedding-profiles/{pid}",
        json={"name": "A2", "base_url": "http://bad/v1", "model": "m"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "EMBEDDING_PROFILE_PROBE_FAILED"


def test_delete(database, monkeypatch):
    client, _ = _app(database, monkeypatch)
    pid = client.post(
        "/api/me/embedding-profiles",
        json={"name": "A", "base_url": "http://a/v1", "model": "m", "api_key": "k"},
    ).json()["id"]
    assert client.delete(f"/api/me/embedding-profiles/{pid}").status_code == 200
    assert client.get("/api/me/embedding-profiles").json()["profiles"] == []


def test_update_keeps_key_secret(database, monkeypatch):
    client, _ = _app(database, monkeypatch)
    pid = client.post(
        "/api/me/embedding-profiles",
        json={"name": "A", "base_url": "http://a/v1", "model": "m", "api_key": "sk-keep"},
    ).json()["id"]
    r = client.put(
        f"/api/me/embedding-profiles/{pid}",
        json={"name": "A2", "base_url": "http://a/v1", "model": "m"},
    )
    assert r.status_code == 200
    assert "sk-keep" not in r.text and r.json()["has_key"] is True
