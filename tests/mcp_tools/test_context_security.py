from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastmcp import Client
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


def _bound(workspace_service, active_workspace_repo=None):
    return context.use_mcp_context(
        context.build_mcp_context(
            workspace_service,
            cast(Any, SimpleNamespace()),
            cast(Any, active_workspace_repo or SimpleNamespace(get=lambda *_: None)),
            cast(Any, SimpleNamespace()),
        )
    )


async def test_session_state_identity_mismatch_is_cleared(monkeypatch):
    fake = FakeContext({"active_workspace": "notes", "active_user_id": "user-a"})
    monkeypatch.setattr(context, "require_user_id", lambda: _resolve_as("user-b"))

    with _bound(WorkspaceService(["notes"])), pytest.raises(ToolError, match="identity changed"):
        await context.active_workspace(cast(Context, fake))

    assert fake.state == {}


async def test_session_state_uses_current_matching_identity(monkeypatch):
    fake = FakeContext({"active_workspace": "notes", "active_user_id": "user-a"})
    monkeypatch.setattr(context, "require_user_id", lambda: _resolve_as("user-a"))

    with _bound(WorkspaceService(["notes"])):
        workspace = await context.active_workspace(cast(Context, fake))

    assert workspace.owner_id == "user-a"
    assert workspace.path == "/workspaces/user-a/notes"


async def test_revoked_workspace_grant_clears_session_state(monkeypatch):
    fake = FakeContext({"active_workspace": "notes", "active_user_id": "user-a"})
    monkeypatch.setattr(context, "require_user_id", lambda: _resolve_as("user-a"))

    with _bound(WorkspaceService([])), pytest.raises(ToolError):
        await context.active_workspace(cast(Context, fake))

    assert fake.state == {}


async def test_db_rehydration_revalidates_workspace_grant(monkeypatch):
    fake = FakeContext({})
    monkeypatch.setattr(context, "require_user_id", lambda: _resolve_as("user-a"))

    with (
        _bound(WorkspaceService([]), SimpleNamespace(get=lambda user_id, scope, *args: "revoked")),
        pytest.raises(ToolError),
    ):
        await context.active_workspace(cast(Context, fake))

    assert fake.state == {}


@pytest.mark.xfail(
    strict=True,
    reason="Active workspace is still stored in the shared per-user fallback (#243).",
)
async def test_conversations_do_not_clobber_workspace_when_the_session_is_lost(
    workspaces_dir, mcp_server, git_workspace_factory
):
    """A reconnecting conversation must retain its target despite another conversation.

    FastMCP's in-process transport gives each Client a distinct session. Re-opening A
    models the request after a client loses its session identifier, which exposes the
    current per-user fallback.
    """
    mcp, _ = mcp_server
    for name in ("ws-a", "ws-b"):
        git_workspace_factory(f"workspaces/u1/{name}")
        mcp_server.workspace_repo.grant_access("u1", name)

    async with Client(mcp) as client_a:
        await client_a.call_tool("activate_workspace", {"name": "ws-a"})
    async with Client(mcp) as client_b:
        await client_b.call_tool("activate_workspace", {"name": "ws-b"})
    async with Client(mcp) as client_a_after_reconnect:
        await client_a_after_reconnect.call_tool(
            "save_note", {"title": "A stays in A", "content": "body"}
        )

    assert list((workspaces_dir / "ws-a").rglob("*.md"))
    assert not list((workspaces_dir / "ws-b").rglob("*.md"))


@pytest.mark.xfail(
    strict=True,
    reason="A sessionless request overwrites the shared per-user fallback (#243).",
)
async def test_sessionless_conversation_cannot_retarget_another_conversation(
    monkeypatch, workspaces_dir, mcp_server, git_workspace_factory
):
    """Claude.ai may omit Mcp-Session-Id on a later request."""
    mcp, _ = mcp_server
    for name in ("ws-a", "ws-b"):
        git_workspace_factory(f"workspaces/u1/{name}")
        mcp_server.workspace_repo.grant_access("u1", name)

    async with Client(mcp) as client_a:
        await client_a.call_tool("activate_workspace", {"name": "ws-a"})

    monkeypatch.setattr(context, "_context_session_id", lambda _ctx: None)
    async with Client(mcp) as client_b:
        await client_b.call_tool("activate_workspace", {"name": "ws-b"})
    monkeypatch.undo()

    async with Client(mcp) as client_a_after_reconnect:
        await client_a_after_reconnect.call_tool(
            "save_note", {"title": "A survives sessionless B", "content": "body"}
        )

    assert list((workspaces_dir / "ws-a").rglob("*.md"))
    assert not list((workspaces_dir / "ws-b").rglob("*.md"))
