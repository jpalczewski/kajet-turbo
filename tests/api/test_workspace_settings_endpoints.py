"""Tests for GET/PATCH /api/workspaces/{name}/settings."""

import pytest

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
    assert "validate_links" in keys
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
