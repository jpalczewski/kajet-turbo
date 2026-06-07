import os
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider


def create_auth() -> InMemoryOAuthProvider:
    base_url = os.environ["MCP_BASE_URL"]
    return InMemoryOAuthProvider(base_url=base_url)
