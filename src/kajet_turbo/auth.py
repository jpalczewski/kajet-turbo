import os
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastmcp.server.auth.providers.in_memory import (
    DEFAULT_AUTH_CODE_EXPIRY_SECONDS,
    InMemoryOAuthProvider,
)
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull

from kajet_turbo.log import logger
from kajet_turbo.repositories.oauth import OAuthRepository

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

    def __init__(self, oauth_repo: OAuthRepository, **kwargs):
        super().__init__(**kwargs)
        self._oauth_repo = oauth_repo
        self._pending: dict[str, tuple[OAuthClientInformationFull, AuthorizationParams]] = {}
        self._restore_state()

    def _restore_state(self) -> None:
        try:
            self._oauth_repo.delete_expired_tokens()
        except Exception:
            logger.exception("oauth_restore: failed to delete expired tokens")

        for row in self._oauth_repo.get_valid_pending():
            try:
                client = OAuthClientInformationFull.model_validate_json(row["client_json"])
                params = AuthorizationParams.model_validate_json(row["params_json"])
                self._pending[row["pending_id"]] = (client, params)
            except Exception:
                logger.exception(
                    "oauth_restore: failed to restore pending authorization",
                    pending_id=row.get("pending_id", "")[:8],
                )

        for data in self._oauth_repo.get_all_registered_clients():
            try:
                client = OAuthClientInformationFull.model_validate_json(data)
                self.clients[client.client_id] = client
            except Exception:
                logger.exception("oauth_restore: failed to restore registered client")

        for row in self._oauth_repo.get_valid_refresh_tokens():
            try:
                self.refresh_tokens[row["token"]] = RefreshToken(
                    token=row["token"],
                    client_id=row["client_id"],
                    scopes=row["scopes"],
                    expires_at=row["expires_at"],
                )
            except Exception:
                logger.exception(
                    "oauth_restore: failed to restore refresh token",
                    client_id=row.get("client_id", ""),
                    token_prefix=row.get("token", "")[:8],
                )

        for row in self._oauth_repo.get_valid_access_tokens():
            try:
                self.access_tokens[row["token"]] = AccessToken(
                    token=row["token"],
                    client_id=row["client_id"],
                    scopes=row["scopes"],
                    expires_at=row["expires_at"],
                )
                if row["refresh_token"]:
                    self._access_to_refresh_map[row["token"]] = row["refresh_token"]
                    self._refresh_to_access_map[row["refresh_token"]] = row["token"]
            except Exception:
                logger.exception(
                    "oauth_restore: failed to restore access token",
                    client_id=row.get("client_id", ""),
                    token_prefix=row.get("token", "")[:8],
                )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await super().register_client(client_info)
        self._oauth_repo.upsert_registered_client(client_info.client_id, client_info.model_dump_json())

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ):
        result = await super().exchange_authorization_code(client, authorization_code)
        at = self.access_tokens.get(result.access_token)
        rt = self.refresh_tokens.get(result.refresh_token) if result.refresh_token else None
        if rt:
            self._oauth_repo.upsert_refresh_token(rt.token, rt.client_id, rt.scopes, rt.expires_at)
        if at:
            self._oauth_repo.upsert_access_token(
                at.token, at.client_id, at.scopes, at.expires_at, result.refresh_token
            )
        return result

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        # Capture old token values BEFORE super() calls _revoke_internal(),
        # which removes them from the in-memory maps.
        old_refresh = refresh_token.token
        old_access = self._refresh_to_access_map.get(old_refresh)

        result = await super().exchange_refresh_token(client, refresh_token, scopes)

        at = self.access_tokens.get(result.access_token)
        rt = self.refresh_tokens.get(result.refresh_token) if result.refresh_token else None
        if rt:
            self._oauth_repo.upsert_refresh_token(rt.token, rt.client_id, rt.scopes, rt.expires_at)
        if at:
            self._oauth_repo.upsert_access_token(
                at.token, at.client_id, at.scopes, at.expires_at, result.refresh_token
            )

        self._oauth_repo.delete_refresh_token(old_refresh)
        if old_access:
            self._oauth_repo.delete_access_token(old_access)

        return result

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        pending_id = secrets.token_urlsafe(32)
        self._pending[pending_id] = (client, params)
        self._oauth_repo.upsert_pending(
            pending_id,
            client.model_dump_json(),
            params.model_dump_json(),
            time.time() + 600,
        )
        return f"/login?pending={pending_id}"

    async def complete_authorization(self, pending_id: str, user_id: str | None = None) -> str:
        """Complete auth after successful login. Returns redirect URI with code."""
        if pending_id not in self._pending:
            row = self._oauth_repo.get_pending(pending_id)
            if row is None:
                raise ValueError("Invalid or expired authorization")
            client = OAuthClientInformationFull.model_validate_json(row[0])
            params = AuthorizationParams.model_validate_json(row[1])
            self._pending[pending_id] = (client, params)
        client, params = self._pending.pop(pending_id)
        self._oauth_repo.delete_pending(pending_id)

        if user_id:
            self._oauth_repo.record_client_authorization(client.client_id, user_id)

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


def create_auth(oauth_repo: OAuthRepository) -> KajetOAuthProvider:
    return KajetOAuthProvider(
        oauth_repo=oauth_repo,
        base_url=_resolve_base_url().rstrip("/") + "/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )
