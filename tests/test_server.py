"""Role-based app factories expose disjoint route sets."""

from kajet_turbo.server import build_api_app, build_mcp_app


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
