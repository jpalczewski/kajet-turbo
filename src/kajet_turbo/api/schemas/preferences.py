from typing import ClassVar

from pydantic import BaseModel

from kajet_turbo.api.schemas.base import RequestModel
from kajet_turbo.errors import ErrorCode, PreferencesError
from kajet_turbo.preferences import Locale


class UserPreferences(BaseModel):
    timezone: str
    locale: Locale


class UpdatePreferencesRequest(RequestModel):
    # Both fields default to None so an omitted key stays out of `model_fields_set` --
    # the route uses that (not the resolved value) to tell "field absent" (no-op) from
    # "field present but null" (must 422, not silently no-op). See api/preferences.py.
    # A wrong-type "timezone" hits "string_type" and an unsupported "locale" value hits
    # the closed `Locale` enum's own "enum" check before the route runs -- both map back
    # to the legacy PREFERENCES_INVALID_INPUT code the frontend already keys off of.
    legacy_error_codes: ClassVar[dict[str, ErrorCode]] = {
        "timezone": PreferencesError.INVALID_INPUT,
        "locale": PreferencesError.INVALID_INPUT,
    }

    timezone: str | None = None
    locale: Locale | None = None
