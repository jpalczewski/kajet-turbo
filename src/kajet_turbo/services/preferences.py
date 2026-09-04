"""Per-user timezone/locale settings: validate against zoneinfo's IANA database and
the closed Locale set, then delegate persistence to UserRepository."""

from kajet_turbo.api.schemas import UserPreferences
from kajet_turbo.models import User
from kajet_turbo.preferences import Locale, is_valid_timezone
from kajet_turbo.repositories.users import UserRepository


class PreferencesService:
    def __init__(self, repo: UserRepository):
        self._repo = repo

    @staticmethod
    def _view(user: User) -> UserPreferences:
        return UserPreferences(timezone=user.timezone, locale=Locale(user.locale))

    def get_preferences(self, user_id: str) -> UserPreferences:
        user = self._repo.get(user_id)
        if user is None:
            raise ValueError(f"user not found: {user_id!r}")
        return self._view(user)

    def update_preferences(
        self, user_id: str, *, timezone: str | None = None, locale: str | None = None
    ) -> UserPreferences:
        if timezone is not None and not is_valid_timezone(timezone):
            raise ValueError(f"unknown IANA timezone: {timezone!r}")
        if locale is not None and locale not in set(Locale):
            raise ValueError(f"unsupported locale: {locale!r}")
        user = self._repo.update_preferences(user_id, timezone=timezone, locale=locale)
        if user is None:
            raise ValueError(f"user not found: {user_id!r}")
        return self._view(user)
