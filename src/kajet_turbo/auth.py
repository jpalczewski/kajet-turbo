import os
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastmcp.server.auth.auth import OAuthProvider
from fastmcp.server.auth.providers.in_memory import (
    DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS,
    DEFAULT_AUTH_CODE_EXPIRY_SECONDS,
)
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from kajet_turbo import identity
from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logger
from kajet_turbo.repositories.oauth import (
    OAuthRepository,
    OAuthTokenPair,
    RotationOutcome,
)

PENDING_AUTHORIZATION_EXPIRY_SECONDS = 600
REFRESH_TOKEN_EXPIRY_SECONDS = 30 * 24 * 3600

_ph = PasswordHasher()
DUMMY_PASSWORD_HASH = _ph.hash("kajet-login-timing-equalizer")


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


class OwnedAuthorizationCode(AuthorizationCode):
    """An authorization code plus the user who consented to it.

    The SDK hands whatever ``load_authorization_code`` returns straight to
    ``exchange_authorization_code``, which is how the owner reaches token issuance.
    Identity must travel with the credential — deriving it from client_id instead lets
    a later authorization of the same client re-point every token already issued."""

    user_id: str | None = None


class FamilyRefreshToken(RefreshToken):
    """SDK refresh token plus the server-side rotation-family metadata."""

    family_id: str
    consumed_at: int | None = None


class KajetOAuthProvider(OAuthProvider):
    """Stateless OAuth provider: the DB is the single source of truth.

    No per-process state means any number of uvicorn workers behind a
    round-robin proxy (and container restarts) observe identical clients,
    pending authorizations, auth codes and tokens. Single-use guarantees are
    enforced by atomic DB deletes, not by in-memory membership checks.
    """

    def __init__(self, oauth_repo: OAuthRepository, **kwargs):
        super().__init__(**kwargs)
        self._oauth_repo = oauth_repo
        logger.info("oauth_provider_init", base_url=str(self.base_url))
        try:
            # Sync call is fine here: __init__ runs at process startup,
            # before the event loop serves requests.
            self._oauth_repo.delete_expired_tokens()
        except Exception:
            logger.exception("oauth_init: failed to delete expired tokens")

    # --- clients ---

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = await run_sync(self._oauth_repo.get_registered_client, client_id)
        if data is None:
            return None
        return OAuthClientInformationFull.model_validate_json(data)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if (
            client_info.scope is not None
            and self.client_registration_options is not None
            and self.client_registration_options.valid_scopes is not None
        ):
            invalid = set(client_info.scope.split()) - set(
                self.client_registration_options.valid_scopes
            )
            if invalid:
                raise ValueError(f"Requested scopes are not valid: {', '.join(invalid)}")
        if client_info.client_id is None:
            raise ValueError("client_id is required for client registration")
        await run_sync(
            self._oauth_repo.upsert_registered_client,
            client_info.client_id,
            client_info.model_dump_json(),
        )

    # --- authorization (login form) flow ---

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        pending_id = secrets.token_urlsafe(32)
        await run_sync(
            self._oauth_repo.upsert_pending,
            pending_id,
            client.model_dump_json(),
            params.model_dump_json(),
            time.time() + PENDING_AUTHORIZATION_EXPIRY_SECONDS,
        )
        return f"/login?pending={pending_id}"

    def get_pending_client(self, pending_id: str) -> OAuthClientInformationFull | None:
        """Blocking; call via run_sync() from async endpoints."""
        row = self._oauth_repo.get_pending(pending_id)
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate_json(row[0])

    async def complete_authorization(self, pending_id: str, user_id: str | None = None) -> str:
        """Complete auth after successful login. Returns redirect URI with code."""
        row = await run_sync(self._oauth_repo.get_pending, pending_id)
        if row is None:
            raise ValueError("Invalid or expired authorization")
        client = OAuthClientInformationFull.model_validate_json(row[0])
        params = AuthorizationParams.model_validate_json(row[1])
        await run_sync(self._oauth_repo.delete_pending, pending_id)
        if client.client_id is None:
            raise ValueError("Stored OAuth client is missing client_id")

        if user_id:
            await run_sync(self._oauth_repo.record_client_authorization, client.client_id, user_id)

        auth_code_value = f"kajet_{secrets.token_hex(16)}"
        scopes_list = params.scopes if params.scopes is not None else []
        if client.scope:
            client_allowed_scopes = set(client.scope.split())
            scopes_list = [s for s in scopes_list if s in client_allowed_scopes]

        await run_sync(
            self._oauth_repo.upsert_auth_code,
            auth_code_value,
            client.client_id,
            user_id,
            str(params.redirect_uri),
            params.redirect_uri_provided_explicitly,
            scopes_list,
            time.time() + DEFAULT_AUTH_CODE_EXPIRY_SECONDS,
            params.code_challenge,
        )
        return construct_redirect_uri(
            str(params.redirect_uri), code=auth_code_value, state=params.state
        )

    # --- auth codes ---

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        row = await run_sync(self._oauth_repo.get_auth_code, authorization_code)
        if row is None or row["client_id"] != client.client_id:
            return None
        if row["expires_at"] < time.time():
            await run_sync(self._oauth_repo.delete_auth_code, authorization_code)
            return None
        return OwnedAuthorizationCode(
            code=row["code"],
            client_id=row["client_id"],
            user_id=row["user_id"],
            redirect_uri=row["redirect_uri"],
            redirect_uri_provided_explicitly=bool(row["redirect_uri_provided_explicitly"]),
            scopes=row["scopes"],
            expires_at=row["expires_at"],
            code_challenge=row["code_challenge"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Atomic delete makes the DB the single-use arbiter: with concurrent
        # exchanges on different workers exactly one observes True, replays
        # (including on the worker that issued the code) get invalid_grant.
        deleted = await run_sync(self._oauth_repo.delete_auth_code, authorization_code.code)
        if not deleted:
            raise TokenError("invalid_grant", "Authorization code not found or already used.")
        if client.client_id is None:
            raise TokenError("invalid_client", "Client ID is required")
        user_id = getattr(authorization_code, "user_id", None)
        if user_id is None:
            # An unowned code cannot mint an owned token; issuing one would recreate the
            # credential-without-an-owner state this whole change removes.
            raise TokenError("invalid_grant", "Authorization code is not bound to a user.")
        return await self._issue_token_pair(client.client_id, authorization_code.scopes, user_id)

    # --- tokens ---

    async def _issue_token_pair(
        self, client_id: str, scopes: list[str], user_id: str
    ) -> OAuthToken:
        now = int(time.time())
        pair = self._new_token_pair(
            client_id,
            scopes,
            user_id,
            family_id=secrets.token_hex(16),
            refresh_expires_at=now + REFRESH_TOKEN_EXPIRY_SECONDS,
            now=now,
        )
        await run_sync(self._oauth_repo.save_token_pair, pair)
        return self._token_response(pair)

    @staticmethod
    def _new_token_pair(
        client_id: str,
        scopes: list[str],
        user_id: str,
        *,
        family_id: str,
        refresh_expires_at: int,
        now: int,
    ) -> OAuthTokenPair:
        return OAuthTokenPair(
            access_token=f"kajet_at_{secrets.token_hex(32)}",
            refresh_token=f"kajet_rt_{secrets.token_hex(32)}",
            client_id=client_id,
            user_id=user_id,
            scopes=scopes,
            access_expires_at=now + DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS,
            refresh_expires_at=refresh_expires_at,
            family_id=family_id,
        )

    @staticmethod
    def _token_response(pair: OAuthTokenPair) -> OAuthToken:
        return OAuthToken(
            access_token=pair.access_token,
            token_type="Bearer",
            expires_in=DEFAULT_ACCESS_TOKEN_EXPIRY_SECONDS,
            refresh_token=pair.refresh_token,
            scope=" ".join(pair.scopes),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        row = await run_sync(self._oauth_repo.get_refresh_token, refresh_token)
        if row is None or row["client_id"] != client.client_id:
            return None
        if row["user_id"] is None:
            return None
        if identity.token_expired(row):
            await run_sync(self._oauth_repo.revoke_by_refresh_token, refresh_token)
            return None
        if row["consumed_at"] is not None:
            await run_sync(self._oauth_repo.revoke_token_family, row["family_id"])
            logger.warning(
                "oauth_refresh_reuse_detected",
                client_id=row["client_id"],
                user_id=row["user_id"],
                family_id=row["family_id"],
            )
            return None
        return FamilyRefreshToken(
            token=row["token"],
            client_id=row["client_id"],
            subject=str(row["user_id"]),
            scopes=row["scopes"],
            expires_at=row["expires_at"],
            family_id=row["family_id"],
            consumed_at=row["consumed_at"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if not set(scopes).issubset(set(refresh_token.scopes)):
            raise TokenError(
                "invalid_scope", "Requested scopes exceed those authorized by the refresh token."
            )
        if client.client_id is None:
            raise TokenError("invalid_client", "Client ID is required")
        user_id = refresh_token.subject
        if user_id is None:
            raise TokenError("invalid_grant", "Refresh token is not bound to a user.")
        family_id = getattr(refresh_token, "family_id", None)
        if family_id is None:
            raise TokenError("invalid_grant", "Refresh token is not bound to a token family.")

        now = int(time.time())
        refresh_expires_at = refresh_token.expires_at or now + REFRESH_TOKEN_EXPIRY_SECONDS
        pair = self._new_token_pair(
            client.client_id,
            scopes,
            user_id,
            family_id=family_id,
            refresh_expires_at=refresh_expires_at,
            now=now,
        )
        outcome = await run_sync(
            self._oauth_repo.rotate_token_pair, refresh_token.token, pair, now=now
        )
        if outcome is RotationOutcome.REUSED:
            logger.warning(
                "oauth_refresh_reuse_detected",
                client_id=client.client_id,
                user_id=user_id,
                family_id=family_id,
            )
        if outcome is not RotationOutcome.ROTATED:
            raise TokenError("invalid_grant", "Refresh token not found or already used.")
        return self._token_response(pair)

    # ty false positive: generic AccessTokenT vs concrete AccessToken — fastmcp's
    # own InMemoryOAuthProvider suppresses the same override diagnostics.
    async def load_access_token(self, token: str) -> AccessToken | None:  # ty: ignore[invalid-method-override]
        row = await run_sync(self._oauth_repo.get_access_token, token)
        if row is None:
            logger.warning("oauth_token_rejected", token_prefix=token[:8], reason="unknown_token")
            return None
        if row["user_id"] is None:
            # Predates the user_id column, so there is no way to tell whose it is.
            # Rejecting here (401) is what makes the client re-run OAuth — failing later
            # in _resolve_user would only surface as a tool error, which clients retry
            # forever instead of re-authorizing.
            logger.warning("oauth_token_rejected", token_prefix=token[:8], reason="no_owner")
            return None
        if identity.token_expired(row):
            logger.warning(
                "oauth_token_rejected",
                token_prefix=token[:8],
                reason="expired",
                expired_s=int(time.time() - row["expires_at"]),
                client_id=row["client_id"],
            )
            # Remove only the AT row — the paired RT must survive so the
            # client can refresh.
            await run_sync(self._oauth_repo.delete_access_token, token)
            return None
        return AccessToken(
            token=row["token"],
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=row["expires_at"],
            subject=str(row["user_id"]),
        )

    async def verify_token(self, token: str) -> AccessToken | None:  # ty: ignore[invalid-method-override]
        return await self.load_access_token(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            await run_sync(self._oauth_repo.revoke_by_access_token, token.token)
        else:
            await run_sync(self._oauth_repo.revoke_by_refresh_token, token.token)


def create_auth(oauth_repo: OAuthRepository) -> KajetOAuthProvider:
    return KajetOAuthProvider(
        oauth_repo=oauth_repo,
        base_url=_resolve_base_url().rstrip("/") + "/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
    )
