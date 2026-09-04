from enum import StrEnum
from functools import cache
from zoneinfo import available_timezones


class Locale(StrEnum):
    PL = "pl"
    EN = "en"


DEFAULT_TIMEZONE = "Europe/Warsaw"
DEFAULT_LOCALE = Locale.PL


@cache
def known_timezones() -> frozenset[str]:
    """Cached: zoneinfo.available_timezones() re-scans tzpath on every call."""
    return frozenset(available_timezones())


def is_valid_timezone(value: str) -> bool:
    """Exact membership only — never ZoneInfo(value) construction. macOS's filesystem
    is case-insensitive, so ZoneInfo("europe/warsaw") passes locally and fails on Linux."""
    return value in known_timezones()
