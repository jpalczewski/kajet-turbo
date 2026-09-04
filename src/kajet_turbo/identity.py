"""Credential -> identity resolution: the single definition of who a request is.

Pure and uncached by design. Every auth caller resolves per request against these
functions; the only cache sits in the logging layer, because caching an authz decision
would let a revoked token or a logged-out session keep working for the cache TTL. If a
future change wants to speed auth up, it must do so without a cache here.

Repositories arrive as parameters rather than imports. Two DI mechanisms coexist in this
codebase — the ``dependencies`` module globals and ``mcp.context.configure_mcp_context``,
which the MCP tool tests populate with their own per-test repositories — so reaching for
either from inside this module would resolve some callers against the wrong database.
The parameter also keeps this module free of a ``dependencies`` import, which would be a
cycle (dependencies -> auth -> identity) and builds a Database at import time.
"""

import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kajet_turbo.repositories.oauth import OAuthRepository
    from kajet_turbo.repositories.sessions import SessionRepository

SESSION_COOKIE = "kajet_session"


def session_token_from_cookies(cookies: Mapping[str, str]) -> str:
    return cookies.get(SESSION_COOKIE, "")


def bearer_token_from_headers(headers: Mapping[str, str]) -> str:
    """The token from an ``Authorization: Bearer <token>`` header, or ""."""
    header = headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return ""
    return header[7:].strip()


def resolve_session_user(session_repo: SessionRepository, token: str) -> dict | None:
    """Session cookie -> ``{"id", "email", "timezone", "locale"}``. Expiry is enforced
    in SQL by the repo."""
    if not token:
        return None
    return session_repo.get_user(token)


def resolve_session_user_from_cookies(
    session_repo: SessionRepository, cookies: Mapping[str, str]
) -> dict | None:
    """Cookie jar -> ``{"id", "email", "timezone", "locale"}``, or None. The one place
    callers hand a raw jar instead of an already-extracted token."""
    return resolve_session_user(session_repo, session_token_from_cookies(cookies))


def token_expired(row: Mapping[str, Any], *, now: float | None = None) -> bool:
    """Whether an OAuth token row (access or refresh) has expired.

    The one definition of that rule. ``get_access_token``/``get_refresh_token`` return
    rows without an expiry predicate, so every caller has to apply it — and when one of
    them forgot, logs started crediting users whose token auth had just rejected. What
    a caller *does* about an expired token differs (auth deletes the row and logs the
    rejection, the log path just shrugs); only the predicate is shared.

    A NULL ``expires_at`` never expires. The comparison is strict: a token expiring
    exactly now is still valid.
    """
    expires_at = row["expires_at"]
    return expires_at is not None and expires_at < (time.time() if now is None else now)


def resolve_bearer_user_id(
    oauth_repo: OAuthRepository, token: str, *, now: float | None = None
) -> str | None:
    """Bearer token -> the user it was issued to, rejecting expired tokens.

    Identity comes from the token row, never from ``client_authorizations``. That table is
    keyed on ``client_id`` alone and written with INSERT OR REPLACE, so resolving through
    it meant "the last user who authorized this client": a second user consenting to the
    same client silently re-pointed every token already issued to it at them, which turned
    one consent click into account takeover. It is a consent ledger now, not an identity
    source.

    Side-effect free, unlike ``KajetOAuthProvider.load_access_token``: it neither deletes
    the expired row nor emits ``oauth_token_rejected``. Callers that only want to know who
    someone is must not mutate state or double-log the auth path's own decision.

    A token predating the user_id column resolves to None — it cannot be attributed, so it
    must not authenticate anyone; that client re-authorizes once.
    """
    if not token:
        return None
    row = oauth_repo.get_access_token(token)
    if row is None or token_expired(row, now=now):
        return None
    return row["user_id"]
