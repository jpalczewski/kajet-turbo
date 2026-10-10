"""get_related_notes (#212): the MCP face of NoteRelatedService.related_async.

Ranking and readiness are proven in tests/services/test_related_notes_service.py; here we
pin the boundary — addressing through NOTE_TARGET, argument validation, forwarding, and the
wire shape. Each mcp_server case is expensive, so cases that only vary the fake's answer
share one fixture instead of being parametrized over it.
"""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from kajet_turbo.repositories.notes import RelatedNotesResult
from kajet_turbo.services.notes import NoteRelatedService
from tests.helpers import (
    RELATED_ITEM_JSON,
    FakeRelatedService,
    add_notes,
    related_item,
    related_note,
)
from tests.mcp_tools.helpers import call_json, seed_note


def _fake(monkeypatch, result) -> FakeRelatedService:
    # The tool closes over the service instance built in conftest, so patch the class.
    fake = FakeRelatedService(result)
    monkeypatch.setattr(NoteRelatedService, "related_async", fake.related_async)
    return fake


async def test_get_related_notes_reports_every_readiness_state(
    workspaces_dir, mcp_server, monkeypatch
):
    mcp, _ = mcp_server
    fake = _fake(monkeypatch, None)
    async with Client(mcp) as client:
        note_id = (await seed_note(client, workspace="test-ws", title="Dinner"))["note_id"]
        fake.result = RelatedNotesResult("ready", [related_item()], 3, 3, 8)
        ready = await call_json(client, "get_related_notes", {"note_id": note_id})
        not_ready = {}
        for status in ("ready", "pending", "unavailable", "empty"):
            fake.result = RelatedNotesResult(status, [], 0, 0, 8)
            not_ready[status] = await call_json(client, "get_related_notes", {"note_id": note_id})

    assert ready == {"status": "ready", "items": [RELATED_ITEM_JSON]}
    assert not_ready == {s: {"status": s, "items": []} for s in not_ready}
    # Defaults: whole workspace, MCP's own limit of 10 (the service/REST default is 5),
    # and the workspace is the one NOTE_TARGET derived from the note, not caller input.
    assert set(fake.calls) == {(note_id, "u1", "test-ws", None, 10)}


async def test_get_related_notes_validates_and_forwards_folder_and_limit(
    workspaces_dir, mcp_server, monkeypatch
):
    mcp, _ = mcp_server
    fake = _fake(monkeypatch, RelatedNotesResult("ready", [], 1, 1, 8))
    async with Client(mcp) as client:
        note_id = (await seed_note(client, workspace="test-ws", title="Dinner"))["note_id"]
        for limit in (0, 51):
            with pytest.raises(ToolError, match="limit"):
                await client.call_tool("get_related_notes", {"note_id": note_id, "limit": limit})
        assert fake.calls == []  # refused before the service was reached

        for limit in (1, 50):
            await client.call_tool("get_related_notes", {"note_id": note_id, "limit": limit})
        await client.call_tool(
            "get_related_notes", {"note_id": note_id, "folder": "Recipes/Soups", "limit": 7}
        )

    assert fake.calls == [
        (note_id, "u1", "test-ws", None, 1),
        (note_id, "u1", "test-ws", None, 50),
        (note_id, "u1", "test-ws", "Recipes/Soups", 7),
    ]


async def test_get_related_notes_real_service_without_backend(workspaces_dir, mcp_server):
    """No fake: the conftest service has no embedding backend, and a bad folder is
    rejected by the shared folder_scope with its message reaching the caller intact."""
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        note_id = (await seed_note(client, workspace="test-ws", title="Dinner"))["note_id"]
        result = await call_json(client, "get_related_notes", {"note_id": note_id})
        with pytest.raises(ToolError, match=r"Invalid folder: '\.\.' not allowed"):
            await client.call_tool("get_related_notes", {"note_id": note_id, "folder": "../x"})

    assert result == {"status": "unavailable", "items": []}


async def test_get_related_notes_hides_other_users_notes(workspaces_dir, mcp_server, monkeypatch):
    """A foreign note and a missing one are indistinguishable, and neither reaches the
    service; a note deleted between resolution and the read (service -> None) reports the
    same not-found."""
    mcp, database = mcp_server
    add_notes(database, related_note("foreign1", owner_id="u2", workspace="other-ws"))
    fake = _fake(monkeypatch, None)
    async with Client(mcp) as client:
        errors = {}
        for note_id in ("foreign1", "missing1"):
            with pytest.raises(ToolError) as excinfo:
                await client.call_tool("get_related_notes", {"note_id": note_id})
            errors[note_id] = str(excinfo.value)
        assert fake.calls == []

        note_id = (await seed_note(client, workspace="test-ws", title="Dinner"))["note_id"]
        with pytest.raises(ToolError) as raced:
            await client.call_tool("get_related_notes", {"note_id": note_id})

    assert errors == {
        "foreign1": "Note not found: note_id=foreign1",
        "missing1": "Note not found: note_id=missing1",
    }
    assert str(raced.value) == f"Note not found: note_id={note_id}"


async def test_get_related_notes_schema(mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        tool = {t.name: t for t in await client.list_tools()}["get_related_notes"]

    props = tool.input_schema["properties"]
    assert set(props) == {"note_id", "folder", "limit"}  # no workspace, no target
    assert tool.input_schema["required"] == ["note_id"]
    assert (props["limit"]["default"], props["limit"]["minimum"], props["limit"]["maximum"]) == (
        10,
        1,
        50,
    )
    assert tool.annotations.read_only_hint is True
