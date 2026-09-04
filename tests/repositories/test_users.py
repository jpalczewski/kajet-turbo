from kajet_turbo.db import Database
from kajet_turbo.repositories.users import UserRepository


def test_create_defaults_preferences(database: Database):
    repo = UserRepository(database.engine)
    user_id = repo.create("u1@example.com", "hash")
    user = repo.get(user_id)
    assert user is not None
    assert user.timezone == "Europe/Warsaw"
    assert user.locale == "pl"


def test_get_missing_user_returns_none(database: Database):
    repo = UserRepository(database.engine)
    assert repo.get("nonexistent") is None


def test_update_preferences_partial(database: Database):
    repo = UserRepository(database.engine)
    user_id = repo.create("u2@example.com", "hash")

    updated = repo.update_preferences(user_id, timezone="America/New_York")
    assert updated is not None
    assert updated.timezone == "America/New_York"
    assert updated.locale == "pl"  # untouched

    updated = repo.update_preferences(user_id, locale="en")
    assert updated is not None
    assert updated.timezone == "America/New_York"  # untouched
    assert updated.locale == "en"


def test_update_preferences_missing_user_returns_none(database: Database):
    repo = UserRepository(database.engine)
    assert repo.update_preferences("nonexistent", timezone="UTC") is None
