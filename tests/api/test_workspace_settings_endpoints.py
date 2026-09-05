"""Tests for GET/PATCH /api/workspaces/{name}/settings."""

from pathlib import Path

import pytest

from kajet_turbo.services.targets import WorkspaceTarget
from tests.api.conftest import ApiTestContext


@pytest.fixture
def client(api_client_factory) -> ApiTestContext:
    return api_client_factory()


@pytest.fixture
def ws_name() -> str:
    return "test-ws"


@pytest.fixture
def other_client(api_client_factory) -> ApiTestContext:
    return api_client_factory(user_id="u2", grant_access=False)


def test_get_settings_returns_definitions_and_defaults(client, ws_name):
    res = client.get(f"/api/workspaces/{ws_name}/settings")
    assert res.status_code == 200
    body = res.json()
    keys = {d["key"] for d in body["definitions"]}
    assert {"include_in_search_all", "validate_links"} <= keys
    assert body["values"]["include_in_search_all"] is True
    assert body["values"]["validate_links"] is True


def test_patch_settings_updates_value(client, ws_name):
    res = client.patch(
        f"/api/workspaces/{ws_name}/settings", json={"values": {"validate_links": False}}
    )
    assert res.status_code == 200
    assert res.json()["values"]["validate_links"] is False
    # Persisted.
    assert (
        client.get(f"/api/workspaces/{ws_name}/settings").json()["values"]["validate_links"]
        is False
    )


def test_patch_settings_can_exclude_workspace_from_search_all(client, ws_name):
    res = client.patch(
        f"/api/workspaces/{ws_name}/settings",
        json={"values": {"include_in_search_all": False}},
    )
    assert res.status_code == 200
    assert res.json()["values"]["include_in_search_all"] is False


def test_patch_settings_rejects_unknown_key(client, ws_name):
    res = client.patch(f"/api/workspaces/{ws_name}/settings", json={"values": {"ghost": True}})
    assert res.status_code == 422


def test_patch_settings_rejects_wrong_type(client, ws_name):
    res = client.patch(
        f"/api/workspaces/{ws_name}/settings", json={"values": {"validate_links": "yes"}}
    )
    assert res.status_code == 422


def test_settings_requires_access(other_client, ws_name):
    # A client authenticated as a different user without access.
    assert other_client.get(f"/api/workspaces/{ws_name}/settings").status_code == 403


def test_temporal_backfill_preview_and_apply(client, ws_name):
    target = WorkspaceTarget(owner_id="u1", name=ws_name, path=Path(client.workspace))
    note_id = client.note_service.save(target, "2026-03-22", "body", [])["note_id"]

    preview = client.post(f"/api/workspaces/{ws_name}/settings/temporal-backfill/preview")

    assert preview.status_code == 200
    candidates = preview.json()["candidates"]
    assert candidates[0]["note_id"] == note_id
    assert candidates[0]["field"] == "occurred_at"
    applied = client.post(
        f"/api/workspaces/{ws_name}/settings/temporal-backfill/apply",
        json={"candidates": candidates},
    )
    assert applied.status_code == 200
    assert applied.json() == {"applied": 1}
