from pydantic import BaseModel

from kajet_turbo.preferences import Locale


class UserPreferences(BaseModel):
    timezone: str
    locale: Locale
