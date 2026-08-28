"""Role-based app factories and SPA fallback behaviour."""

from fastapi import FastAPI
from starlette.testclient import TestClient

from kajet_turbo.health import add_health_routes
from kajet_turbo.server import (
    _is_spa_navigation,
    _SPAFiles,
    build_api_app,
    build_app,
    build_mcp_app,
)


def _route_paths(app) -> set[str]:
    # build_mcp_app wraps the FastAPI in _MCPPathFix; unwrap to read routes.
    inner = getattr(app, "_app", app)
    return {getattr(route, "path", "") for route in inner.routes}


def test_mcp_app_mounts_mcp_not_api():
    paths = _route_paths(build_mcp_app())
    assert "/mcp" in paths
    assert not any(p.startswith("/api") for p in paths)


def test_api_app_has_api_not_mcp():
    paths = _route_paths(build_api_app())
    assert any(p.startswith("/api") for p in paths)
    assert "/mcp" not in paths


def test_api_app_does_not_mount_spa_when_disabled(monkeypatch):
    monkeypatch.setenv("KAJET_SERVE_SPA", "0")

    paths = _route_paths(build_api_app())

    assert "/" not in paths


def test_spa_navigation_policy_handles_assets_and_dotted_explorer_folders():
    html_get = {"method": "GET", "headers": [(b"accept", b"text/html")]}

    assert _is_spa_navigation("workspace/ws/notes/2026.08", html_get)
    assert _is_spa_navigation("some/client/route", html_get)
    assert not _is_spa_navigation(".env", html_get)
    assert not _is_spa_navigation("workspace/ws/notes/.git", html_get)
    assert not _is_spa_navigation("workspace/ws/other/notes/wp-config.php", html_get)
    assert not _is_spa_navigation("workspace/bad.slug/notes/wp-config.php", html_get)
    assert not _is_spa_navigation("missing.js", html_get)
    assert not _is_spa_navigation("some/client/route", {"method": "GET", "headers": []})


def test_spa_mount_only_falls_back_for_browser_navigation(tmp_path):
    (tmp_path / "index.html").write_text("<h1>SPA shell</h1>")
    (tmp_path / "app.js").write_text("console.log('asset')")
    app = FastAPI()
    app.mount("/", _SPAFiles(str(tmp_path)))

    with TestClient(app) as client:
        navigation = client.get("/some/client/route", headers={"accept": "text/html"})
        head = client.head("/some/client/route", headers={"accept": "text/html"})
        hidden = client.get("/.env", headers={"accept": "text/html"})
        asset_like = client.get("/missing.js")
        no_accept = client.get("/some/client/route")
        post = client.post("/some/client/route")
        dotted_folder = client.get("/workspace/ws/notes/2026.08", headers={"accept": "text/html"})
        existing_asset = client.get("/app.js")

    assert navigation.status_code == 200
    assert navigation.text == "<h1>SPA shell</h1>"
    assert head.status_code == 200
    assert head.content == b""
    assert hidden.status_code == 404
    assert asset_like.status_code == 404
    assert no_accept.status_code == 404
    assert post.status_code == 404
    assert dotted_folder.status_code == 200
    assert existing_asset.status_code == 200
    assert existing_asset.text == "console.log('asset')"


def test_role_apps_expose_health_routes():
    for app in (build_api_app(), build_mcp_app(), build_app()):
        paths = _route_paths(app)
        assert "/healthz" in paths
        assert "/readyz" in paths


def test_healthz_returns_ok_without_cache(database):
    app = FastAPI()
    add_health_routes(app, engine=database.engine)

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_readyz_returns_ok_for_database(database):
    app = FastAPI()
    add_health_routes(app, engine=database.engine)

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"db": "ok"}}
    assert response.headers["cache-control"] == "no-store"


def test_readyz_returns_503_when_database_ping_fails():
    class BrokenEngine:
        def connect(self):
            raise RuntimeError("database unavailable")

    app = FastAPI()
    add_health_routes(app, engine=BrokenEngine())

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "checks": {"db": "error"}}
