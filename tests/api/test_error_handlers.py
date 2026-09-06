"""Exercises the shared exception-handler infrastructure (#247) directly, independent
of any particular route's business logic -- these assert the envelope contract itself."""

from typing import ClassVar

import pytest
from fastapi import APIRouter
from pydantic import BaseModel
from starlette.testclient import TestClient

from kajet_turbo.api.errors import install_error_handlers
from kajet_turbo.api.schemas.base import RequestModel
from kajet_turbo.errors import ErrorCode, FolderError, RequestError
from tests.api.conftest import build_test_app
from tests.helpers import entries_named, make_logging_app, read_log_entries


class _ProbeBody(BaseModel):
    name: str


# Two unrelated models sharing a field name -- the (model, field) keying (#341) must keep
# a legacy code declared on one from leaking onto the other's identically-named field.
class _ProbeWithFolderCode(RequestModel):
    legacy_error_codes: ClassVar[dict[str, ErrorCode]] = {"folder": FolderError.PATH_REQUIRED}

    folder: str


class _ProbeWithoutFolderCode(RequestModel):
    folder: str


class _ProbeBatch(BaseModel):
    # No legacy_error_codes of its own -- proves a nested item's error resolves against
    # the item model, not the wrapper (mirrors BatchCreateNotesRequest/CreateNoteRequest).
    items: list[_ProbeWithFolderCode]


_router = APIRouter()


@_router.post("/probe/json")
def _probe_json(body: _ProbeBody) -> dict:
    return {"ok": True, "name": body.name}


@_router.post("/probe/folder-with-code")
def _probe_folder_with_code(body: _ProbeWithFolderCode) -> dict:
    return {"folder": body.folder}


@_router.post("/probe/folder-without-code")
def _probe_folder_without_code(body: _ProbeWithoutFolderCode) -> dict:
    return {"folder": body.folder}


@_router.post("/probe/batch")
def _probe_batch(body: _ProbeBatch) -> dict:
    return {"count": len(body.items)}


@_router.get("/probe/boom")
def _probe_boom() -> dict:
    raise RuntimeError("kaboom -- must never reach the response body or the logs")


def test_malformed_json_returns_400():
    client = TestClient(build_test_app(routers=(_router,)))
    # FastAPI only attempts request.json() (and can raise json_invalid) when the
    # content-type says JSON; without it, the raw bytes go straight to Pydantic and
    # fail as a non-dict body (422, covered by test_non_object_body_returns_422).
    resp = client.post(
        "/probe/json", content=b"{not valid json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_INPUT"


def test_non_object_body_returns_422():
    client = TestClient(build_test_app(routers=(_router,)))
    resp = client.post("/probe/json", json=["a", "b"])
    assert resp.status_code == 422
    assert resp.json()["error"] == "INVALID_INPUT"


def test_missing_required_field_returns_422_naming_the_field():
    client = TestClient(build_test_app(routers=(_router,)))
    resp = client.post("/probe/json", json={})
    assert resp.status_code == 422
    assert resp.json()["error"] == "INVALID_INPUT"
    assert "name" in resp.json()["detail"]


def test_missing_field_code_is_scoped_to_the_declaring_model():
    client = TestClient(build_test_app(routers=(_router,)))
    resp = client.post("/probe/folder-with-code", json={})
    assert resp.status_code == 422
    assert resp.json()["error"] == "FOLDER_PATH_REQUIRED"


def test_missing_field_without_a_declared_code_falls_back_to_generic_invalid_input():
    # Same field name, same failure shape, different (undeclared) model -- proves the
    # code above came from _ProbeWithFolderCode.legacy_error_codes, not a bare "folder"
    # match that would also fire here.
    client = TestClient(build_test_app(routers=(_router,)))
    resp = client.post("/probe/folder-without-code", json={})
    assert resp.status_code == 422
    assert resp.json()["error"] == "INVALID_INPUT"


def test_nested_batch_item_field_resolves_against_the_item_model():
    client = TestClient(build_test_app(routers=(_router,)))
    resp = client.post("/probe/batch", json={"items": [{}]})
    assert resp.status_code == 422
    assert resp.json()["error"] == "FOLDER_PATH_REQUIRED"


def test_legacy_error_codes_rejects_unknown_field_at_class_definition():
    with pytest.raises(TypeError, match="unknown field"):

        class _BadProbe(RequestModel):
            legacy_error_codes: ClassVar[dict[str, ErrorCode]] = {
                "nope": RequestError.INVALID_INPUT
            }

            title: str


def test_unexpected_exception_returns_generic_500_with_no_payload_leak():
    client = TestClient(build_test_app(routers=(_router,)), raise_server_exceptions=False)
    resp = client.get("/probe/boom")
    assert resp.status_code == 500
    assert resp.json() == {"error": "internal_error"}
    assert "kaboom" not in resp.text


def test_unexpected_exception_logs_request_id(capsys):
    app = make_logging_app()
    app.include_router(_router)
    install_error_handlers(app)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/probe/boom")

    assert resp.status_code == 500
    (entry,) = entries_named(read_log_entries(capsys), "unhandled_exception")
    assert isinstance(entry["request_id"], str) and len(entry["request_id"]) == 8
    assert entry["exc_type"] == "RuntimeError"
