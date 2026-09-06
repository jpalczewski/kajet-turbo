from enum import StrEnum


class AuthError(StrEnum):
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    ACCESS_DENIED = "ACCESS_DENIED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    PENDING_EXPIRED = "PENDING_EXPIRED"


class SecurityEvent(StrEnum):
    """Stable messages for records retained in the security audit stream."""

    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"
    PERMISSION_DENIED = "permission_denied"


class SecurityReason(StrEnum):
    """Private, structured diagnostics for security audit events."""

    BAD_CREDENTIALS = "bad_credentials"
    UNKNOWN_EMAIL = "unknown_email"
    NO_SESSION = "no_session"
    EXPIRED_PENDING = "expired_pending"
    UNKNOWN_TOKEN = "unknown_token"
    NO_OWNER = "no_owner"
    EXPIRED = "expired"
    MISSING_ROW = "missing_row"
    WRONG_OWNER = "wrong_owner"
    WORKSPACE_ACCESS_DENIED = "workspace_access_denied"
    WORKSPACE_MISMATCH = "workspace_mismatch"
