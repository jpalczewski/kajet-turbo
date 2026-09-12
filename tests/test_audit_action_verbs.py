"""Denied write operations must be audited with a write verb, not note.read (#281).

The shared REST/MCP target dependencies back both reads and writes, and every
denial used to be logged as `note.read` / `workspace.read` — so a denied DELETE
looked like a harmless read denial in the audit log. Write call sites now opt
into `note.write` / `workspace.write` explicitly; these tests pin the logged
action on both sides of that contract, driving the dependencies directly with a
denying resolver stub (no HTTP/MCP boundary needed for the verb itself).
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastmcp.exceptions import ToolError

import kajet_turbo.dependencies as rest_deps
import kajet_turbo.mcp.context as mcp_context
import kajet_turbo.services.targets as targets_mod
from kajet_turbo.dependencies import CurrentUser
from kajet_turbo.errors.auth import SecurityReason as DenialReason
from kajet_turbo.errors.targets import TargetError
from kajet_turbo.mcp.context import McpDependencies, use_mcp_context
from kajet_turbo.services.targets import (
    NoteTarget,
    TargetFailure,
    TargetResolutionError,
    TargetResolver,
    WorkspaceTarget,
)

USER = CurrentUser(id="u-caller", email="u@example.com", timezone="UTC", locale="en")
WS = WorkspaceTarget(owner_id="u-caller", name="ws", path=Path("/tmp/ws"))


def _capture(monkeypatch, module) -> list:
    """Collect log_permission_denied kwargs from every audit path in `module`."""
    captured: list = []
    monkeypatch.setattr(
        module, "log_permission_denied", MagicMock(side_effect=lambda *a, **k: captured.append(k))
    )
    return captured


def _actions(captured: list) -> list:
    return [c.get("action") for c in captured]


def _denied_note_resolver() -> MagicMock:
    failure = TargetFailure(None, TargetError.NOT_FOUND, DenialReason.WRONG_OWNER)
    resolver = MagicMock(spec=TargetResolver)
    resolver.note.side_effect = TargetResolutionError(failure)
    return resolver


def _denied_workspace_resolver() -> MagicMock:
    failure = TargetFailure(None, TargetError.NOT_FOUND, DenialReason.WORKSPACE_ACCESS_DENIED)
    resolver = MagicMock(spec=TargetResolver)
    resolver.workspace.side_effect = TargetResolutionError(failure)
    return resolver


# --- REST: resolve_note_target ---


def test_rest_denied_write_logs_note_write(monkeypatch):
    captured = _capture(monkeypatch, targets_mod)
    with pytest.raises(HTTPException) as exc:
        rest_deps.RESOLVE_NOTE_WRITE.dependency("ws", "n1", WS, _denied_note_resolver(), USER)
    assert exc.value.status_code == 404
    assert _actions(captured) == ["note.write"]


def test_rest_denied_read_still_logs_note_read(monkeypatch):
    captured = _capture(monkeypatch, targets_mod)
    with pytest.raises(HTTPException) as exc:
        rest_deps.resolve_note_target("ws", "n1", WS, _denied_note_resolver(), USER)
    assert exc.value.status_code == 404
    assert _actions(captured) == ["note.read"]


def test_rest_workspace_mismatch_on_write_logs_note_write(monkeypatch):
    # The mismatch branch logs directly from dependencies.py (not via audit_denied).
    captured = _capture(monkeypatch, rest_deps)
    other_ws = WorkspaceTarget(owner_id="u-caller", name="other", path=Path("/tmp/other"))
    resolver = MagicMock(spec=TargetResolver)
    resolver.note.return_value = NoteTarget(note_id="n1", workspace=other_ws)
    with pytest.raises(HTTPException) as exc:
        rest_deps.RESOLVE_NOTE_WRITE.dependency("ws", "n1", WS, resolver, USER)
    assert exc.value.status_code == 404
    assert _actions(captured) == ["note.write"]


# --- REST: resolve_workspace_target ---


def test_rest_denied_workspace_write_logs_workspace_write(monkeypatch):
    captured = _capture(monkeypatch, targets_mod)
    with pytest.raises(HTTPException) as exc:
        rest_deps.RESOLVE_WORKSPACE_WRITE.dependency("ws", USER, _denied_workspace_resolver())
    assert exc.value.status_code == 403
    assert _actions(captured) == ["workspace.write"]


def test_rest_denied_workspace_read_still_logs_workspace_read(monkeypatch):
    captured = _capture(monkeypatch, targets_mod)
    with pytest.raises(HTTPException) as exc:
        rest_deps.resolve_workspace_target("ws", USER, _denied_workspace_resolver())
    assert exc.value.status_code == 403
    assert _actions(captured) == ["workspace.read"]


# --- MCP: resolve_note_target / resolve_workspace_target ---


def _mcp_deps_with(resolver: MagicMock) -> McpDependencies:
    return McpDependencies(
        workspace_service=MagicMock(),
        oauth_repo=MagicMock(),
        event_repo=MagicMock(),
        post_commit_hooks=MagicMock(),
        target_resolver=resolver,
        user_repo=MagicMock(),
    )


async def test_mcp_denied_write_logs_note_write(monkeypatch):
    captured = _capture(monkeypatch, targets_mod)
    dep = mcp_context.resolve_note_target_for("note.write")
    with (
        use_mcp_context(_mcp_deps_with(_denied_note_resolver())),
        pytest.raises(ToolError, match="Note not found"),
    ):
        await dep("n1", "u-caller")
    assert _actions(captured) == ["note.write"]


async def test_mcp_denied_read_still_logs_note_read(monkeypatch):
    captured = _capture(monkeypatch, targets_mod)
    with (
        use_mcp_context(_mcp_deps_with(_denied_note_resolver())),
        pytest.raises(ToolError, match="Note not found"),
    ):
        await mcp_context.resolve_note_target("n1", "u-caller")
    assert _actions(captured) == ["note.read"]


async def test_mcp_denied_workspace_write_logs_workspace_write(monkeypatch):
    captured = _capture(monkeypatch, targets_mod)
    dep = mcp_context.resolve_workspace_target_for("workspace.write")
    with (
        use_mcp_context(_mcp_deps_with(_denied_workspace_resolver())),
        pytest.raises(ToolError, match="not accessible"),
    ):
        await dep("ws", "u-caller")
    assert _actions(captured) == ["workspace.write"]
