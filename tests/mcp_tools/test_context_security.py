from types import SimpleNamespace
from typing import cast

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context

from kajet_turbo.mcp import context


class FakeContext:
    def __init__(self, state: dict[str, str | None], session_id: str = "session-1"):
        self.state = state
        self.session_id = session_id

    async def get_state(self, key: str):
        return self.state.get(key)

    async def set_state(self, key: str, value):
        self.state[key] = value

    async def delete_state(self, key: str):
        self.state.pop(key, None)


class WorkspaceService:
    def __init__(self, accessible: list[str]):
        self.accessible = accessible

    def list_accessible(self, user_id: str) -> list[str]:
        return self.accessible

    def workspace_path(self, user_id: str, name: str) -> str:
        return f"/workspaces/{user_id}/{name}"


async def _resolve_as(user_id: str) -> str:
    return user_id


async def test_session_state_identity_mismatch_is_cleared(monkeypatch):
    fake = FakeContext({"active_workspace": "notes", "active_user_id": "user-a"})
    monkeypatch.setattr(context.deps, "workspace_service", WorkspaceService(["notes"]))
    monkeypatch.setattr(context, "require_user_id", lambda: _resolve_as("user-b"))

    with pytest.raises(ToolError, match="identity changed"):
        await context.active_workspace(cast(Context, fake))

    assert fake.state == {}


async def test_session_state_uses_current_matching_identity(monkeypatch):
    fake = FakeContext({"active_workspace": "notes", "active_user_id": "user-a"})
    monkeypatch.setattr(context.deps, "workspace_service", WorkspaceService(["notes"]))
    monkeypatch.setattr(context, "require_user_id", lambda: _resolve_as("user-a"))

    workspace = await context.active_workspace(cast(Context, fake))

    assert workspace.owner_id == "user-a"
    assert workspace.path == "/workspaces/user-a/notes"


async def test_revoked_workspace_grant_clears_session_state(monkeypatch):
    fake = FakeContext({"active_workspace": "notes", "active_user_id": "user-a"})
    monkeypatch.setattr(context.deps, "workspace_service", WorkspaceService([]))
    monkeypatch.setattr(context, "require_user_id", lambda: _resolve_as("user-a"))

    with pytest.raises(ToolError):
        await context.active_workspace(cast(Context, fake))

    assert fake.state == {}


async def test_db_rehydration_revalidates_workspace_grant(monkeypatch):
    fake = FakeContext({})
    monkeypatch.setattr(context.deps, "workspace_service", WorkspaceService([]))
    monkeypatch.setattr(
        context.deps,
        "active_workspace_repo",
        SimpleNamespace(get=lambda user_id, scope, *args: "revoked"),
    )
    monkeypatch.setattr(context, "require_user_id", lambda: _resolve_as("user-a"))

    with pytest.raises(ToolError):
        await context.active_workspace(cast(Context, fake))

    assert fake.state == {}
