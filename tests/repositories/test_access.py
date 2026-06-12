from kajet_turbo.db import Database
from kajet_turbo.repositories.sessions import SessionRepository
from kajet_turbo.repositories.users import UserRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository


def test_user_repository_create_and_get(database: Database):
    repository = UserRepository(database.engine)

    user_id = repository.create("a@b.com", "hash123")

    user = repository.get_by_email("a@b.com")
    assert len(user_id) == 12
    assert user is not None
    assert user.email == "a@b.com"
    assert user.password_hash == "hash123"
    assert repository.count() == 1


def test_session_repository(database: Database):
    users = UserRepository(database.engine)
    sessions = SessionRepository(database.engine)
    user_id = users.create("x@y.com", "hash")

    token = sessions.create(user_id)

    user = sessions.get_user(token)
    assert len(token) == 64
    assert user is not None
    assert user["email"] == "x@y.com"
    sessions.delete(token)
    assert sessions.get_user(token) is None


def test_workspace_repository(database: Database):
    users = UserRepository(database.engine)
    workspaces = WorkspaceRepository(database.engine)
    user_id = users.create("u@v.com", "hash")

    workspaces.grant_access(user_id, "ws-alpha")

    assert workspaces.has_access(user_id, "ws-alpha")
    assert not workspaces.has_access(user_id, "ws-beta")
    assert workspaces.list_user_workspaces(user_id) == ["ws-alpha"]
