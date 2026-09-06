"""One place for every app-level exception -> HTTP response mapping.

Registered identically on every FastAPI app the project builds (`server.py`'s
production apps and `tests/api/conftest.py`'s test app) via :func:`install_error_handlers`,
so a route's error contract never depends on which harness is exercising it.

`ValueError`, `FileNotFoundError` and `FileExistsError` are deliberately not mapped here:
their meaning is route-specific and stays a local `except` until a route's service layer
raises a typed error (#253/#254).

`_request_validation_handler` also carries a small `PydanticCustomError` type -> legacy
error code table, so a handful of request models (`CreateNoteRequest.title`,
`CreateFolderRequest.path`, `MoveNoteRequest.folder`) can move validation into Pydantic
without changing the machine-readable code the frontend already keys UI copy off of.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.requests import Request

from kajet_turbo.errors import FolderError, NoteError, RequestError
from kajet_turbo.errors import GitError as GitErrorCode
from kajet_turbo.log import logger
from kajet_turbo.markdown import BrokenWikilinkError
from kajet_turbo.repositories.git import GitError
from kajet_turbo.workspace import InvalidFolderError, TemporalMetadataError


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


# Pydantic error "type" -> legacy error code, for validators (schemas/notes/crud.py) whose
# old hand-rolled 422s the frontend still keys UI copy off of (grep frontend/src/lib/api
# before removing an entry here). Everything else falls back to RequestError.INVALID_INPUT.
_CUSTOM_ERROR_TYPES: dict[str, NoteError | FolderError] = {
    "note_title_required": NoteError.TITLE_REQUIRED,
    "folder_path_required": FolderError.PATH_REQUIRED,
    "folder_path_invalid": FolderError.PATH_INVALID,
}
# CreateNoteRequest.title and CreateFolderRequest.path are required fields (min_length=1 /
# no default), so OpenAPI advertises them correctly as non-optional -- but that means a
# *missing* key never reaches the field_validator that raises the custom types above
# (pydantic doesn't run a validator against an absent required field). Map FastAPI's own
# missing/wrong-type errors for these fields back to the same legacy codes by field name
# instead. MoveNoteRequest.folder shares the "folder" entry: "" is itself a legitimate
# value there (move to root), so only a genuinely missing/wrong-type key hits this table --
# an empty string still reaches the route and NoteFolderService.move as a real value.
_REQUIRED_FIELD_CODES: dict[str, NoteError | FolderError] = {
    "title": NoteError.TITLE_REQUIRED,
    "path": FolderError.PATH_REQUIRED,
    "folder": FolderError.PATH_REQUIRED,
}
_REQUIRED_ERROR_TYPES = {"missing", "string_type", "string_too_short"}


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
    field = str(loc[-1]) if loc else ""
    if error_type in _REQUIRED_ERROR_TYPES and field in _REQUIRED_FIELD_CODES:
        code: NoteError | FolderError | RequestError = _REQUIRED_FIELD_CODES[field]
    else:
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


async def _temporal_metadata_handler(request: Request, exc: TemporalMetadataError) -> JSONResponse:
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
    app.add_exception_handler(TemporalMetadataError, _temporal_metadata_handler)  # ty: ignore[invalid-argument-type]
    app.add_exception_handler(GitError, _git_error_handler)  # ty: ignore[invalid-argument-type]
    app.add_exception_handler(Exception, _unexpected_exception_handler)
