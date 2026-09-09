"""Tests for GET/PATCH /api/workspaces/{name}/settings."""

from pathlib import Path

import pytest

from kajet_turbo import workspace_settings as ws_settings
from kajet_turbo.api.schemas import UpdateWorkspaceSettingsValues
from kajet_turbo.services.targets import WorkspaceTarget
from tests.api.conftest import ApiTestContext


def test_update_settings_values_model_covers_every_registry_key():
    # Guards against a new setting being added to REGISTRY without a matching field on
    # UpdateWorkspaceSettingsValues -- without this, extra="forbid" would silently 422 any
    # attempt to PATCH the new setting over REST, with nothing else failing to say so.
    assert set(UpdateWorkspaceSettingsValues.model_fields) == set(ws_settings.REGISTRY)


@pytest.fixture
def client(api_client_factory) -> ApiTestContext:
    return api_client_factory()


@pytest.fixture
def ws_name() -> str:
    return "test-ws"


@pytest.fixture
def other_client(api_client_factory) -> ApiTestContext:
    return api_client_factory(user_id="u2", grant_access=False)


@pytest.fixture
def anon_client(api_client_factory) -> ApiTestContext:
    return api_client_factory(user_id=None)


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


def test_get_settings_requires_auth_401(anon_client, ws_name):
    assert anon_client.get(f"/api/workspaces/{ws_name}/settings").status_code == 401


def test_patch_settings_requires_auth_401(anon_client, ws_name):
    r = anon_client.patch(
        f"/api/workspaces/{ws_name}/settings", json={"values": {"validate_links": False}}
    )
    assert r.status_code == 401


def test_patch_settings_no_access_403(other_client, ws_name):
    r = other_client.patch(
        f"/api/workspaces/{ws_name}/settings", json={"values": {"validate_links": False}}
    )
    assert r.status_code == 403


def test_patch_settings_omitted_key_is_a_no_op(client, ws_name):
    client.patch(f"/api/workspaces/{ws_name}/settings", json={"values": {"validate_links": False}})
    res = client.patch(f"/api/workspaces/{ws_name}/settings", json={"values": {}})
    assert res.status_code == 200
    # validate_links stays False -- an empty `values` dict changes nothing, matching the
    # pre-#254 per-key iteration's behavior for a key the client didn't mention.
    assert res.json()["values"]["validate_links"] is False
    assert res.json()["values"]["include_in_search_all"] is True


def test_temporal_backfill_preview_and_apply(client, ws_name):
    target = WorkspaceTarget(owner_id="u1", name=ws_name, path=Path(client.workspace))
    note_id = client.note_service.create.save(target, "2026-03-22", "body", [])["note_id"]

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


def test_temporal_backfill_preview_requires_auth_401(anon_client, ws_name):
    r = anon_client.post(f"/api/workspaces/{ws_name}/settings/temporal-backfill/preview")
    assert r.status_code == 401


def test_temporal_backfill_preview_no_access_403(other_client, ws_name):
    r = other_client.post(f"/api/workspaces/{ws_name}/settings/temporal-backfill/preview")
    assert r.status_code == 403


def test_temporal_backfill_apply_requires_auth_401(anon_client, ws_name):
    r = anon_client.post(
        f"/api/workspaces/{ws_name}/settings/temporal-backfill/apply",
        json={"candidates": []},
    )
    assert r.status_code == 401


def test_temporal_backfill_apply_no_access_403(other_client, ws_name):
    r = other_client.post(
        f"/api/workspaces/{ws_name}/settings/temporal-backfill/apply",
        json={"candidates": []},
    )
    assert r.status_code == 403


def test_temporal_backfill_apply_rejects_empty_candidates_422(client, ws_name):
    # An empty batch is a client mistake (nothing to apply), not a staleness conflict --
    # this must land as 422/WORKSPACE_INVALID_INPUT, not the 409 the stale-preview path uses.
    res = client.post(
        f"/api/workspaces/{ws_name}/settings/temporal-backfill/apply",
        json={"candidates": []},
    )
    assert res.status_code == 422
    assert res.json()["error"] == "WORKSPACE_INVALID_INPUT"


def test_temporal_backfill_apply_stale_preview_409(client, ws_name):
    target = WorkspaceTarget(owner_id="u1", name=ws_name, path=Path(client.workspace))
    client.note_service.create.save(target, "2026-03-22", "body", [])

    preview = client.post(f"/api/workspaces/{ws_name}/settings/temporal-backfill/preview")
    candidates = preview.json()["candidates"]
    first = client.post(
        f"/api/workspaces/{ws_name}/settings/temporal-backfill/apply",
        json={"candidates": candidates},
    )
    assert first.status_code == 200

    # Re-applying the same (now-outdated) preview batch must be rejected as a conflict,
    # distinct from the 422 malformed-input path above, with a machine-readable code.
    second = client.post(
        f"/api/workspaces/{ws_name}/settings/temporal-backfill/apply",
        json={"candidates": candidates},
    )
    assert second.status_code == 409
    assert second.json()["error"] == "WORKSPACE_BACKFILL_STALE"
