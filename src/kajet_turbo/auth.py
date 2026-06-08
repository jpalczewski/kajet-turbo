import os
from datetime import datetime, timezone
from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
from mcp.server.auth.settings import ClientRegistrationOptions
from kajet_turbo.storage import Storage


def _resolve_base_url() -> str:
    raw = (
        os.environ.get("MCP_BASE_URL")
        or os.environ.get("COOLIFY_FQDN")
        or os.environ.get("COOLIFY_URL")
    )
    if not raw:
        raise RuntimeError("Set MCP_BASE_URL or deploy via Coolify (COOLIFY_FQDN/COOLIFY_URL)")
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    return raw


def create_auth() -> InMemoryOAuthProvider:
    return InMemoryOAuthProvider(
        base_url=_resolve_base_url(),
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )


def save_oauth_client(
    storage: Storage,
    client_id: str,
    client_secret: str,
    redirect_uris: list[str],
) -> None:
    storage.save_oauth_client(
        client_id,
        client_secret,
        redirect_uris,
        datetime.now(timezone.utc).isoformat(),
    )


def get_oauth_client(storage: Storage, client_id: str) -> dict | None:
    return storage.get_oauth_client(client_id)
