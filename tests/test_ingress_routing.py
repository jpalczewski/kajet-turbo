"""The ingress allowlist must keep `/metrics` internal (#311).

Caddy proxies `/mcp/*` and `/api/*` to the role apps, so a metrics route under either
prefix would be public. `/metrics` at the root of each app matches no proxy matcher and
falls through to the static handlers, so it is only reachable on the compose network.
There is no Caddy in the test environment; the CI ingress smoke test hits the packaged
image for the real 404.
"""

import re
from fnmatch import fnmatchcase
from pathlib import Path

import pytest

_CADDYFILE = Path(__file__).parent.parent / "Caddyfile"


def _path_matcher(name: str) -> list[str]:
    match = re.search(rf"^\s*@{name} path (.+)$", _CADDYFILE.read_text(), re.MULTILINE)
    assert match, f"@{name} path matcher not found in Caddyfile"
    return match.group(1).split()


def _matches(name: str, request_path: str) -> bool:
    # fnmatch's `*` also crosses `/`, a superset of Caddy's globbing — so a negative
    # result here is conservative.
    return any(fnmatchcase(request_path.lower(), pattern) for pattern in _path_matcher(name))


@pytest.mark.parametrize("matcher", ["mcp", "api", "docs"])
def test_root_metrics_is_not_proxied(matcher: str):
    assert not _matches(matcher, "/metrics")


@pytest.mark.parametrize(
    ("request_path", "matcher"), [("/mcp/metrics", "mcp"), ("/api/metrics", "api")]
)
def test_prefixed_metrics_paths_are_proxied_so_the_apps_must_404_them(
    request_path: str, matcher: str
):
    """Documents why the route is root-only: these do reach the upstream (the role apps
    404 them — see tests/test_metrics.py)."""
    assert _matches(matcher, request_path)
