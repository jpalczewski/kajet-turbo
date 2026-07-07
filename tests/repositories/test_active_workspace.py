from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from kajet_turbo.db import Database
from kajet_turbo.models import ActiveWorkspace
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.users import UserRepository


def test_get_max_age_returns_fresh_row(database: Database):
    users = UserRepository(database.engine)
    repo = ActiveWorkspaceRepository(database.engine)
    user_id = users.create("a@b.com", "hash")

    repo.set(user_id, "ws-alpha")

    assert repo.get(user_id, max_age=timedelta(hours=1)) == "ws-alpha"


def test_get_max_age_excludes_stale_row(database: Database):
    users = UserRepository(database.engine)
    repo = ActiveWorkspaceRepository(database.engine)
    user_id = users.create("a@b.com", "hash")
    stale_at = (datetime.now(UTC) - timedelta(hours=2)).isoformat()

    with Session(database.engine) as session:
        session.add(
            ActiveWorkspace(user_id=user_id, scope="user", workspace="ws-old", updated_at=stale_at)
        )
        session.commit()

    assert repo.get(user_id, max_age=timedelta(hours=1)) is None
    assert repo.get(user_id) == "ws-old"


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


def test_scopes_are_independent(database: Database):
    users = UserRepository(database.engine)
    repo = ActiveWorkspaceRepository(database.engine)
    user_id = users.create("a@b.com", "hash")

    repo.set(user_id, "ws-alpha", scope="mcp-session:a")
    repo.set(user_id, "ws-beta", scope="mcp-session:b")

    assert repo.get(user_id, scope="mcp-session:a") == "ws-alpha"
    assert repo.get(user_id, scope="mcp-session:b") == "ws-beta"
    assert repo.get(user_id) is None


def test_get_unknown_user_returns_none(database: Database):
    repo = ActiveWorkspaceRepository(database.engine)
    assert repo.get("nope") is None
