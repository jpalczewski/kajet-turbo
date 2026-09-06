from pydantic import BaseModel

from kajet_turbo.preferences import Locale


class UserPreferences(BaseModel):
    timezone: str
    locale: Locale


class UpdatePreferencesRequest(BaseModel):
    # Both fields default to None so an omitted key stays out of `model_fields_set` --
    # the route uses that (not the resolved value) to tell "field absent" (no-op) from
    # "field present but null" (must 422, not silently no-op). See api/preferences.py.
    timezone: str | None = None
    locale: Locale | None = None
