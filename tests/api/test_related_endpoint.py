from dataclasses import fields
from pathlib import Path

import pytest

from kajet_turbo.dependencies import get_note_related_service
from kajet_turbo.repositories.notes import RelatedNoteItem, RelatedNotesResult
from kajet_turbo.services.targets import WorkspaceTarget
from kajet_turbo.shared.notes import RelatedNoteResponseItem, RelatedNotesResponse
from kajet_turbo.workspace import InvalidFolderError
from tests.helpers import RELATED_ITEM_JSON, FakeRelatedService, related_item

URL = "/api/workspaces/test-ws/notes/{note_id}/related"


def _ws(ws_path) -> WorkspaceTarget:
    return WorkspaceTarget(owner_id="u1", name="test-ws", path=Path(ws_path))


def test_related_wire_models_match_service_dataclasses():
    # REST and MCP both build the response with model_validate(from_attributes=True), which
    # silently drops a dataclass field the wire model lacks — this is the tie (#441).
    assert {f.name for f in fields(RelatedNoteItem)} == set(RelatedNoteResponseItem.model_fields)
    assert set(RelatedNotesResponse.model_fields) <= {f.name for f in fields(RelatedNotesResult)}


def _use(auth_client, result) -> FakeRelatedService:
    fake = FakeRelatedService(result)
    auth_client.client.app.dependency_overrides[get_note_related_service] = lambda: fake
    return fake


def _note_id(auth_client) -> str:
    ws = _ws(auth_client.workspace)
    saved = auth_client.note_service.create.save(ws, "Dinner", "# Dinner", [])
    return saved["note_id"]


def test_related_ready_serializes_items(auth_client):
    note_id = _note_id(auth_client)
    _use(auth_client, RelatedNotesResult("ready", [related_item()], 3, 3, 8))

    resp = auth_client.client.get(URL.format(note_id=note_id))

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ready",
        "items": [RELATED_ITEM_JSON],
    }


@pytest.mark.parametrize("status", ["ready", "pending", "unavailable", "empty"])
def test_related_states_without_items_serialize_as_200(auth_client, status):
    note_id = _note_id(auth_client)
    _use(auth_client, RelatedNotesResult(status, [], 0, 0, 8))

    resp = auth_client.client.get(URL.format(note_id=note_id))

    assert resp.status_code == 200
    assert resp.json() == {"status": status, "items": []}


def test_related_is_unavailable_without_embedding_profile(auth_client):
    # No fake: the real service wired by the fixture has no embedding backend.
    note_id = _note_id(auth_client)

    resp = auth_client.client.get(URL.format(note_id=note_id))

    assert resp.status_code == 200
    assert resp.json() == {"status": "unavailable", "items": []}


def test_related_forwards_defaults(auth_client):
    note_id = _note_id(auth_client)
    fake = _use(auth_client, RelatedNotesResult("ready", [], 1, 1, 8))

    auth_client.client.get(URL.format(note_id=note_id))

    assert fake.calls == [(note_id, "u1", "test-ws", None, 5)]


def test_related_forwards_folder_and_limit(auth_client):
    note_id = _note_id(auth_client)
    fake = _use(auth_client, RelatedNotesResult("ready", [], 1, 1, 8))

    params = {"folder": "Recipes/Soups", "limit": 7}
    auth_client.client.get(URL.format(note_id=note_id), params=params)

    assert fake.calls == [(note_id, "u1", "test-ws", "Recipes/Soups", 7)]


@pytest.mark.parametrize("limit", ["0", "-1", "51", "abc"])
def test_related_rejects_out_of_range_limit(auth_client, limit):
    note_id = _note_id(auth_client)
    fake = _use(auth_client, RelatedNotesResult("ready", [], 1, 1, 8))

    resp = auth_client.client.get(URL.format(note_id=note_id), params={"limit": limit})

    assert resp.status_code == 422
    assert fake.calls == []


@pytest.mark.parametrize("limit", [1, 50])
def test_related_accepts_limit_bounds(auth_client, limit):
    note_id = _note_id(auth_client)
    _use(auth_client, RelatedNotesResult("ready", [], 1, 1, 8))

    resp = auth_client.client.get(URL.format(note_id=note_id), params={"limit": limit})

    assert resp.status_code == 200


def test_related_invalid_folder_maps_to_422(auth_client):
    note_id = _note_id(auth_client)
    _use(auth_client, InvalidFolderError("Invalid folder: '..' not allowed"))

    resp = auth_client.client.get(URL.format(note_id=note_id), params={"folder": "../x"})

    assert resp.status_code == 422
    assert resp.json()["error"] == "INVALID_FOLDER"


def test_related_service_none_maps_to_404(auth_client):
    note_id = _note_id(auth_client)
    _use(auth_client, None)

    assert auth_client.client.get(URL.format(note_id=note_id)).status_code == 404


def test_related_unknown_note_returns_404(auth_client):
    assert auth_client.client.get(URL.format(note_id="nope")).status_code == 404


def test_related_note_from_other_workspace_returns_404(auth_client):
    other_ws = WorkspaceTarget(owner_id="u1", name="other", path=Path(auth_client.workspace))
    note_id = auth_client.note_service.create.save(other_ws, "Elsewhere", "c", [])["note_id"]

    assert auth_client.client.get(URL.format(note_id=note_id)).status_code == 404


def test_related_returns_403_when_no_access(no_access_client):
    assert no_access_client.get(URL.format(note_id="x")).status_code == 403


def test_related_returns_401_when_anon(anon_client):
    assert anon_client.get(URL.format(note_id="x")).status_code == 401
