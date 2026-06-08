import pytest
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider


def test_create_auth_returns_in_memory_provider(monkeypatch):
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    from kajet_turbo.auth import create_auth

    provider = create_auth()

    assert isinstance(provider, InMemoryOAuthProvider)


def test_create_auth_requires_base_url(monkeypatch):
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    monkeypatch.delenv("COOLIFY_FQDN", raising=False)
    monkeypatch.delenv("COOLIFY_URL", raising=False)
    from kajet_turbo.auth import create_auth

    with pytest.raises(RuntimeError):
        create_auth()


def test_create_auth_fallback_coolify_fqdn(monkeypatch):
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    monkeypatch.setenv("COOLIFY_FQDN", "preview.example.com")
    monkeypatch.delenv("COOLIFY_URL", raising=False)
    from kajet_turbo.auth import create_auth

    provider = create_auth()

    assert isinstance(provider, InMemoryOAuthProvider)


def test_create_auth_fallback_coolify_fqdn_with_protocol(monkeypatch):
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    monkeypatch.setenv("COOLIFY_FQDN", "https://preview.example.com")
    monkeypatch.delenv("COOLIFY_URL", raising=False)
    from kajet_turbo.auth import create_auth

    provider = create_auth()

    assert isinstance(provider, InMemoryOAuthProvider)


def test_create_auth_fallback_coolify_url(monkeypatch):
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    monkeypatch.delenv("COOLIFY_FQDN", raising=False)
    monkeypatch.setenv("COOLIFY_URL", "preview.example.com")
    from kajet_turbo.auth import create_auth

    provider = create_auth()

    assert isinstance(provider, InMemoryOAuthProvider)
