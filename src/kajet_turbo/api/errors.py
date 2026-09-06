"""One place for every app-level exception -> HTTP response mapping.

Registered identically on every FastAPI app the project builds (`server.py`'s
production apps and `tests/api/conftest.py`'s test app) via :func:`install_error_handlers`,
so a route's error contract never depends on which harness is exercising it.

`ValueError`, `FileNotFoundError` and `FileExistsError` are deliberately not mapped here:
their meaning is route-specific and stays a local `except` until a route's service layer
raises a typed error (#253/#254).

`_request_validation_handler` also carries a small `PydanticCustomError` type -> legacy
error code table, so a handful of request models (`CreateNoteRequest.title`,
`CreateFolderRequest.path`, `MoveNoteRequest.folder`, `CreateSshKeyRequest.name`) can move
validation into Pydantic without changing the machine-readable code the frontend already
keys UI copy off of. A field-level failure Pydantic raises itself (missing key, wrong
type, bad enum value) instead of a custom validator is mapped via each request model's own
`legacy_error_codes` (`api/schemas/base.py::RequestModel`) rather than one bare-field-name
table shared by every model in the app -- see #341.
"""

from __future__ import annotations

from typing import get_args, get_origin

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.requests import Request

from kajet_turbo.errors import (
    ErrorCode,
    FolderError,
    NoteError,
    RequestError,
    SshKeyError,
    WorkspaceError,
)
from kajet_turbo.errors import GitError as GitErrorCode
from kajet_turbo.log import logger
from kajet_turbo.markdown import BrokenWikilinkError
from kajet_turbo.repositories.git import GitError
from kajet_turbo.workspace import ExtrasReservedKeyError, InvalidFolderError, TemporalMetadataError


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)
    return JSONResponse(
        status_code=exc.status_code, content={"error": exc.detail}, headers=exc.headers
    )


# Pydantic error "type" -> legacy error code, for validators (schemas/notes/crud.py,
# schemas/ssh_keys.py, schemas/workspaces/meta.py) whose old hand-rolled 422s the frontend
# still keys UI copy off of (grep frontend/src/lib/api before removing an entry here). This
# table is keyed by the validator's own custom `type` string, not a bare field name, so it
# carries no cross-model collision risk the way a by-field-name table would -- each type
# string is already unique to the validator that raises it. Everything else falls back to
# RequestError.INVALID_INPUT.
_CUSTOM_ERROR_TYPES: dict[str, NoteError | FolderError | SshKeyError | WorkspaceError] = {
    "note_title_required": NoteError.TITLE_REQUIRED,
    "folder_path_required": FolderError.PATH_REQUIRED,
    "folder_path_invalid": FolderError.PATH_INVALID,
    "ssh_key_name_required": SshKeyError.NAME_REQUIRED,
    "workspace_name_required": WorkspaceError.NAME_REQUIRED,
}
# A field-level failure Pydantic raises itself -- a missing required key, a wrong-type
# value, an invalid Literal/enum choice -- never reaches a field_validator (pydantic
# doesn't run one against an absent/mistyped field), so it can't raise one of the custom
# types above. These all resolve through each owning request model's own
# `legacy_error_codes` (RequestModel, api/schemas/base.py) instead of one shared
# by-field-name table, so a new field named "title"/"path"/"folder"/... on an unrelated
# model can never silently inherit another model's code (#341).
_FIELD_ERROR_TYPES = {"missing", "string_type", "string_too_short", "literal_error", "enum"}


def _body_model(request: Request) -> type[BaseModel] | None:
    """The single Pydantic body model FastAPI bound this route's request to, if any --
    resolved from the matched route's dependant rather than the (untyped) parsed body, so
    it reflects the declared schema even when validation itself failed."""
    route = request.scope.get("route")
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return None
    body_params = dependant.body_params
    if len(body_params) != 1:
        return None
    annotation = body_params[0].field_info.annotation
    return (
        annotation if isinstance(annotation, type) and issubclass(annotation, BaseModel) else None
    )


def _unwrap_model(annotation: object) -> type[BaseModel] | None:
    """Digs a nested request model out of a field annotation like `list[Model]` or
    `Model | None`, so a batch body's per-item error can resolve to the item model's own
    `legacy_error_codes` instead of the batch wrapper's."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in get_args(annotation) if get_origin(annotation) is not None else ():
        if (nested := _unwrap_model(arg)) is not None:
            return nested
    return None


def _resolve_legacy_code(root: type[BaseModel] | None, loc: tuple[object, ...]) -> ErrorCode | None:
    """Walks `loc` (a RequestValidationError entry's location, e.g.
    `("body", "notes", 0, "title")`) from the request's body model down to whichever
    model actually owns the failing field, then looks the field up in that model's
    `legacy_error_codes` -- not the root model's, so a batch item's field resolves to the
    item model, not the wrapper list field."""
    if root is None or not loc or loc[0] != "body":
        return None
    parts = [part for part in loc[1:] if not isinstance(part, int)]
    if not parts:
        return None
    current = root
    for part in parts[:-1]:
        fields = current.model_fields
        if part not in fields or (nested := _unwrap_model(fields[part].annotation)) is None:
            return None
        current = nested
    return getattr(current, "legacy_error_codes", {}).get(parts[-1])


async def _request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    first = errors[0] if errors else {}
    # FastAPI raises RequestValidationError with a "json_invalid" entry when the body
    # doesn't parse as JSON at all; every other entry is a field-level validation failure.
    status_code = 400 if first.get("type") == "json_invalid" else 422
    loc = first.get("loc", ())
    loc_str = ".".join(str(part) for part in loc if part != "body")
    msg = first.get("msg", "Invalid request body")
    detail = f"{loc_str}: {msg}" if loc_str else msg
    error_type = first.get("type", "")
    code: ErrorCode | None = None
    if error_type in _FIELD_ERROR_TYPES:
        code = _resolve_legacy_code(_body_model(request), loc)
    if code is None:
        code = _CUSTOM_ERROR_TYPES.get(error_type, RequestError.INVALID_INPUT)
    return JSONResponse(
        status_code=status_code,
        content={"error": str(code), "detail": detail},
    )


async def _invalid_folder_handler(request: Request, exc: InvalidFolderError) -> JSONResponse:
    return JSONResponse(
        status_code=422, content={"error": str(FolderError.INVALID_FOLDER), "detail": str(exc)}
    )


async def _broken_wikilink_handler(request: Request, exc: BrokenWikilinkError) -> JSONResponse:
    return JSONResponse(
        status_code=422, content={"error": str(NoteError.BROKEN_WIKILINK), "detail": str(exc)}
    )


async def _invalid_input_handler(
    request: Request, exc: TemporalMetadataError | ExtrasReservedKeyError
) -> JSONResponse:
    return JSONResponse(
        status_code=422, content={"error": str(NoteError.INVALID_INPUT), "detail": str(exc)}
    )


async def _git_error_handler(request: Request, exc: GitError) -> JSONResponse:
    logger.error(
        "git_error",
        request_id=getattr(request.state, "request_id", None),
        error=str(exc),
    )
    return JSONResponse(status_code=500, content={"error": str(GitErrorCode.GIT_ERROR)})


async def _unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled_exception",
        request_id=getattr(request.state, "request_id", None),
        exc_type=type(exc).__qualname__,
        error=str(exc),
    )
    return JSONResponse(status_code=500, content={"error": "internal_error"})


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_exception_handler)  # ty: ignore[invalid-argument-type] — FastAPI accepts narrower exc type at runtime
    app.add_exception_handler(RequestValidationError, _request_validation_handler)  # ty: ignore[invalid-argument-type]
    app.add_exception_handler(InvalidFolderError, _invalid_folder_handler)  # ty: ignore[invalid-argument-type]
    app.add_exception_handler(BrokenWikilinkError, _broken_wikilink_handler)  # ty: ignore[invalid-argument-type]
    app.add_exception_handler(TemporalMetadataError, _invalid_input_handler)  # ty: ignore[invalid-argument-type]
    app.add_exception_handler(ExtrasReservedKeyError, _invalid_input_handler)  # ty: ignore[invalid-argument-type]
    app.add_exception_handler(GitError, _git_error_handler)  # ty: ignore[invalid-argument-type]
    app.add_exception_handler(Exception, _unexpected_exception_handler)
