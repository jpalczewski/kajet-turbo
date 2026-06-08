import os
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.settings import ClientRegistrationOptions


def create_auth() -> InMemoryOAuthProvider:
    base_url = os.environ["MCP_BASE_URL"]
    return InMemoryOAuthProvider(
        base_url=base_url,
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )
