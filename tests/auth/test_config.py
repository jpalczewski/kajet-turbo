import pytest
from fastmcp.server.auth.auth import OAuthProvider

from kajet_turbo.auth import KajetOAuthProvider, create_auth
from kajet_turbo.repositories.oauth import OAuthRepository


def test_create_auth_returns_oauth_provider(
    monkeypatch: pytest.MonkeyPatch, oauth_repository: OAuthRepository
):
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")

    provider = create_auth(oauth_repository)

    assert isinstance(provider, OAuthProvider)
    assert isinstance(provider, KajetOAuthProvider)


def test_create_auth_requires_base_url(
    monkeypatch: pytest.MonkeyPatch, oauth_repository: OAuthRepository
):
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    monkeypatch.delenv("COOLIFY_FQDN", raising=False)
    monkeypatch.delenv("COOLIFY_URL", raising=False)

    with pytest.raises(RuntimeError):
        create_auth(oauth_repository)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("COOLIFY_FQDN", "preview.example.com"),
        ("COOLIFY_FQDN", "https://preview.example.com"),
        ("COOLIFY_URL", "preview.example.com"),
    ],
)
def test_create_auth_uses_coolify_fallback(
    monkeypatch: pytest.MonkeyPatch,
    oauth_repository: OAuthRepository,
    variable: str,
    value: str,
):
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    monkeypatch.delenv("COOLIFY_FQDN", raising=False)
    monkeypatch.delenv("COOLIFY_URL", raising=False)
    monkeypatch.setenv(variable, value)

    assert isinstance(create_auth(oauth_repository), OAuthProvider)
