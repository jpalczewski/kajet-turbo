"""Role-based app factories expose disjoint route sets."""

from fastapi import FastAPI
from starlette.testclient import TestClient

from kajet_turbo.health import add_health_routes
from kajet_turbo.server import build_api_app, build_app, build_mcp_app


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
