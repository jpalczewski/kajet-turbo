import os
import secrets
import time
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastmcp.server.auth.providers.in_memory import (
    DEFAULT_AUTH_CODE_EXPIRY_SECONDS,
    InMemoryOAuthProvider,
)
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    construct_redirect_uri,
)
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull

from kajet_turbo.storage import Storage

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


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


class KajetOAuthProvider(InMemoryOAuthProvider):
    """OAuth provider that shows a real login form instead of auto-approving."""

    def __init__(self, storage: Storage, **kwargs):
        super().__init__(**kwargs)
        self._storage = storage
        self._pending: dict[str, tuple[OAuthClientInformationFull, AuthorizationParams]] = {}

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        pending_id = secrets.token_urlsafe(32)
        self._pending[pending_id] = (client, params)
        return f"/login?pending={pending_id}"

    async def complete_authorization(self, pending_id: str) -> str:
        """Complete auth after successful login. Returns redirect URI with code."""
        if pending_id not in self._pending:
            raise ValueError("Invalid or expired authorization")
        client, params = self._pending.pop(pending_id)

        # Generate auth code (replicates InMemoryOAuthProvider logic)
        auth_code_value = f"kajet_{secrets.token_hex(16)}"
        expires_at = time.time() + DEFAULT_AUTH_CODE_EXPIRY_SECONDS

        scopes_list = params.scopes if params.scopes is not None else []
        if client.scope:
            client_allowed_scopes = set(client.scope.split())
            scopes_list = [s for s in scopes_list if s in client_allowed_scopes]

        self.auth_codes[auth_code_value] = AuthorizationCode(
            code=auth_code_value,
            client_id=client.client_id,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            scopes=scopes_list,
            expires_at=expires_at,
            code_challenge=params.code_challenge,
        )

        return construct_redirect_uri(
            str(params.redirect_uri), code=auth_code_value, state=params.state
        )


def create_auth(storage: Storage) -> KajetOAuthProvider:
    return KajetOAuthProvider(
        storage=storage,
        base_url=_resolve_base_url(),
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )


# Legacy helpers used by existing tests — keep working
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
        datetime.now(UTC).isoformat(),
    )


def get_oauth_client(storage: Storage, client_id: str) -> dict | None:
    return storage.get_oauth_client(client_id)
