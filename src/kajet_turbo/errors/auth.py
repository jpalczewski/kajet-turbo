from enum import StrEnum


class AuthError(StrEnum):
    NOT_AUTHENTICATED = "NOT_AUTHENTICATED"
    ACCESS_DENIED = "ACCESS_DENIED"
