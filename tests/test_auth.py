import pytest
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider


def test_create_auth_returns_in_memory_provider(monkeypatch):
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    from kajet_turbo.auth import create_auth

    provider = create_auth()

    assert isinstance(provider, InMemoryOAuthProvider)


def test_create_auth_requires_base_url(monkeypatch):
    monkeypatch.delenv("MCP_BASE_URL", raising=False)
    from kajet_turbo.auth import create_auth

    with pytest.raises(KeyError):
        create_auth()
