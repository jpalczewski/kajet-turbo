"""Mechanical checks that every /api/ route follows the typed endpoint pattern documented
in src/kajet_turbo/api/CLAUDE.md (#255, phase R4 of the FastAPI endpoint epic #239).

Walks the real route table FastAPI builds from `kajet_turbo.api.api_router` -- the same
object `server.py` and `scripts/export_openapi.py` mount -- rather than grepping source, so
a route that satisfies the letter of a grep (e.g. "response_model=" present) but not its
intent (returning a raw JSONResponse anyway) still gets caught.
"""

import inspect
import json
from functools import lru_cache
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.responses import Response

from scripts.export_openapi import build_schema_app
from tests.helpers import flatten_routes

_BODY_METHODS = {"POST", "PUT", "PATCH"}
_SNAPSHOT_PATH = Path(__file__).parent / "openapi_snapshot.json"

# endpoint "module.qualname" -> reason it's exempt from the checks below. Deliberately
# small: add an entry only when a real check would otherwise fail it, not defensively for
# every route in a category (e.g. oauth.py's routes and public_notes.py's share-link route
# already satisfy every check below without an exemption -- verified when this test was
# written, don't add them just because "OAuth routes" sounds like it should be exempt).
_EXEMPTIONS = {
    "kajet_turbo.api.workspaces.export.api_export_workspace": (
        "file download (FileResponse) -- not a JSON route, response_model doesn't apply"
    ),
}


def _api_routes() -> list[APIRoute]:
    app = build_schema_app()
    # APIWebSocketRoute (/api/ws) is a different route class with no response_model/
    # body_field/dependant.request_param_name to check -- excluded by the isinstance guard,
    # not listed in _EXEMPTIONS, since it never had these properties to exempt.
    return [
        r
        for r in flatten_routes(app.routes)
        if isinstance(r, APIRoute) and r.path.startswith("/api/")
    ]


def _route_id(route: APIRoute) -> str:
    return f"{'|'.join(sorted(route.methods or ()))} {route.path}"


@pytest.mark.parametrize("route", _api_routes(), ids=_route_id)
def test_route_follows_typed_endpoint_pattern(route: APIRoute) -> None:
    methods = route.methods or set()
    # route.endpoint is typed as a plain Callable by FastAPI's stubs, but every endpoint
    # here is a real module-level function -- getattr with a fallback keeps ty happy
    # without a blanket ignore.
    endpoint_key = (
        f"{route.endpoint.__module__}.{getattr(route.endpoint, '__qualname__', route.endpoint)}"
    )
    if endpoint_key in _EXEMPTIONS:
        pytest.skip(_EXEMPTIONS[endpoint_key])

    assert route.response_model is not None, (
        f"{route.path} has no response_model -- FastAPI can't validate or filter its "
        "response; add one or add this route to _EXEMPTIONS with a reason."
    )
    assert isinstance(route.response_model, type) and issubclass(route.response_model, BaseModel), (
        f"{route.path}'s response_model {route.response_model!r} isn't a Pydantic model."
    )

    # A route can declare the right response_model and still bypass it by constructing a
    # raw Response/JSONResponse instead of the model -- FastAPI never validates against
    # response_model in that case. Catch it from the endpoint's own return annotation.
    return_annotation = inspect.signature(route.endpoint).return_annotation
    assert not (isinstance(return_annotation, type) and issubclass(return_annotation, Response)), (
        f"{route.path} returns a raw {return_annotation.__name__} instead of constructing "
        f"and returning its declared {route.response_model.__name__} -- response_model "
        "validation never runs against a Response returned directly."
    )

    body_field = route.body_field
    if body_field is not None:
        assert isinstance(body_field.field_info.annotation, type) and issubclass(
            body_field.field_info.annotation, BaseModel
        ), (
            f"{route.path}'s body is typed {body_field.field_info.annotation!r}, not a "
            "Pydantic model."
        )

    if methods & _BODY_METHODS and body_field is None:
        # No typed body at all on a route whose method conventionally carries a JSON body,
        # plus a bare Request parameter, is the fingerprint of manual `await
        # request.json()` parsing -- the exact pattern this issue retires. A route with no
        # body and no Request (e.g. a POST action that just needs the URL) is fine either
        # way, so this only fires on the combination.
        assert not route.dependant.request_param_name, (
            f"{route.path} takes a bare Request without a typed Pydantic body -- looks "
            "like manual request.json() parsing instead of a typed body model."
        )


@lru_cache(maxsize=1)
def _openapi_schema() -> dict:
    # Shared across every test below that needs the schema (orphan-schema check, ErrorCode
    # regression, snapshot diff) -- app.openapi() walks all ~58 routes and ~97 Pydantic
    # models and measurably costs ~90ms per call, so recomputing it per test wastes real
    # time for a value that's identical within one test run.
    return build_schema_app().openapi()


# FastAPI injects these itself for the default 422 response of every route with request
# validation -- not from api/schemas/, not something this check should ever flag.
_FASTAPI_BUILTIN_SCHEMAS = {"HTTPValidationError", "ValidationError"}


def _all_schema_models() -> set[str]:
    """Every BaseModel subclass loaded from api/schemas/ or kajet_turbo/shared/ (the
    cross-cutting models both REST and MCP responses build on, e.g. GraphBase's
    node/edge item types in shared/notes.py), by class name."""

    def subclasses(cls: type) -> set[type]:
        direct = set(cls.__subclasses__())
        return direct | {s for sub in direct for s in subclasses(sub)}

    return {
        cls.__name__
        for cls in subclasses(BaseModel)
        if cls.__module__.startswith(("kajet_turbo.api.schemas", "kajet_turbo.shared"))
    }


def test_no_orphan_schema_in_openapi_components() -> None:
    """Every object schema FastAPI actually renders should trace back to a BaseModel
    still defined under api/schemas/ or shared/ -- catches a schema kept alive only by
    another otherwise-unused schema referencing it (the PingMessage/ClientMessage failure
    mode: a schema nothing imports doesn't reach this component list at all, so it's a
    grep, not this test, that finds that case -- this test guards the case a plain grep
    can't). Note: kajet_turbo/mcp/notes/types.py defines its own richer
    NoteLinkItem/GraphNoteNode/GraphTagNode for MCP tool output -- same class names as
    the plain REST versions in shared/notes.py, but a different module, and MCP tools
    aren't FastAPI routes so they never reach this OpenAPI schema at all."""
    schema = _openapi_schema()
    # Restrict to object schemas (BaseModel output): an enum schema has "enum", a
    # composite like the ErrorCode union has "anyOf" -- neither has "properties".
    component_names = {
        name
        for name, definition in schema.get("components", {}).get("schemas", {}).items()
        if "properties" in definition
    } - _FASTAPI_BUILTIN_SCHEMAS
    known_models = _all_schema_models()
    orphans = component_names - known_models
    assert not orphans, (
        f"OpenAPI declares object schema(s) {sorted(orphans)} with no matching BaseModel "
        "under api/schemas/ or shared/ -- likely stale/renamed."
    )


def test_error_code_union_renders_as_anyof_of_refs() -> None:
    """ErrorCode (errors/__init__.py) is a PEP 695 `type` alias over 13 StrEnums. Pins down
    what used to be a manual, undocumented check (`app.openapi()`, eyeballed once) as a
    real regression test."""
    schema = _openapi_schema()
    error_code = schema["components"]["schemas"]["ErrorCode"]
    assert "anyOf" in error_code, f"ErrorCode no longer renders as an anyOf: {error_code}"
    refs = {member["$ref"] for member in error_code["anyOf"]}
    expected_members = {
        "AuthError",
        "WorkspaceError",
        "NoteError",
        "FolderError",
        "GitError",
        "JobError",
        "PreferencesError",
        "RequestError",
        "TargetError",
        "WorkspaceRemoteError",
        "SshKeyError",
        "EmbeddingProfileError",
        "ShareLinkError",
    }
    assert refs == {f"#/components/schemas/{name}" for name in expected_members}


def _current_snapshot() -> dict:
    schema = _openapi_schema()
    operations = sorted(
        (path, method.upper(), details["operationId"])
        for path, methods in schema["paths"].items()
        for method, details in methods.items()
        if isinstance(details, dict) and "operationId" in details
    )
    return {
        "operations": [
            {"method": method, "path": path, "operationId": operation_id}
            for path, method, operation_id in operations
        ],
        "schemas": sorted(schema.get("components", {}).get("schemas", {})),
    }


def test_openapi_schema_matches_committed_snapshot() -> None:
    """Pins schema names and operationIds so incidental client churn (a renamed schema, a
    changed operationId that regenerates every call site under a new function name) shows
    up in code review as a diff to this fixture instead of only surfacing downstream in the
    generated frontend client. Update the fixture deliberately, review the diff, then
    commit -- run `uv run python -m tests.api.test_endpoint_contract` to regenerate it (see
    the `if __name__ == "__main__"` block at the bottom of this file); it is not meant to
    auto-update itself."""
    current = _current_snapshot()
    saved = json.loads(_SNAPSHOT_PATH.read_text())

    saved_ops = {(o["method"], o["path"], o["operationId"]) for o in saved["operations"]}
    current_ops = {(o["method"], o["path"], o["operationId"]) for o in current["operations"]}
    added_ops = current_ops - saved_ops
    removed_ops = saved_ops - current_ops

    saved_schemas = set(saved["schemas"])
    current_schemas = set(current["schemas"])
    added_schemas = current_schemas - saved_schemas
    removed_schemas = saved_schemas - current_schemas

    assert not (added_ops or removed_ops or added_schemas or removed_schemas), (
        "OpenAPI schema drifted from tests/api/openapi_snapshot.json.\n"
        f"  operations added:   {sorted(added_ops)}\n"
        f"  operations removed: {sorted(removed_ops)}\n"
        f"  schemas added:      {sorted(added_schemas)}\n"
        f"  schemas removed:    {sorted(removed_schemas)}\n"
        "If this is a deliberate change, regenerate the fixture (see this test's docstring)."
    )


if __name__ == "__main__":
    # `uv run python -m tests.api.test_endpoint_contract` -- regenerates the committed
    # snapshot from the current route table. Review the diff before committing; this
    # script doesn't run as part of the pytest suite.
    _SNAPSHOT_PATH.write_text(json.dumps(_current_snapshot(), indent=2, sort_keys=True) + "\n")
    print(f"Wrote {_SNAPSHOT_PATH}")
