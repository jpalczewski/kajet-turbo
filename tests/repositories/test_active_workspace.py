from kajet_turbo.db import Database
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.users import UserRepository


def test_set_and_get(database: Database):
    users = UserRepository(database.engine)
    repo = ActiveWorkspaceRepository(database.engine)
    user_id = users.create("a@b.com", "hash")

    assert repo.get(user_id) is None

    repo.set(user_id, "ws-alpha")
    assert repo.get(user_id) == "ws-alpha"


def test_set_is_upsert(database: Database):
    users = UserRepository(database.engine)
    repo = ActiveWorkspaceRepository(database.engine)
    user_id = users.create("a@b.com", "hash")

    repo.set(user_id, "ws-alpha")
    repo.set(user_id, "ws-beta")

    assert repo.get(user_id) == "ws-beta"


def test_get_unknown_user_returns_none(database: Database):
    repo = ActiveWorkspaceRepository(database.engine)
    assert repo.get("nope") is None
