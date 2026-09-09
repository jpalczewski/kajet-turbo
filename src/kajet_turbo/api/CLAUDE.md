# REST Endpoint Pattern

`src/kajet_turbo/api/workspaces/notes/crud/notes.py` is the canonical shape every route
under `src/kajet_turbo/api/` should follow (#253, Phase R2 of the FastAPI endpoint epic
#239, completed by #255). New endpoints should copy this pattern.
`tests/api/test_endpoint_contract.py` walks every route and fails if a new one skips it
(no `response_model`, a raw `Response`/`JSONResponse` return, or a hand-parsed body) --
`shared_preview.py`'s `/shared/{token}` is the one deliberate exception, since it serves
HTML rather than JSON and lives outside `/api/`; see that module's docstring.

## Request model, dependency, service call, response model

```python
@router.post(
    "/api/workspaces/{name}/notes",
    status_code=201,
    response_model=CreateNoteResponse,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def api_create_note(
    name: str,
    body: CreateNoteRequest,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    note_create_service: NoteCreateService = Depends(get_note_create_service),
) -> CreateNoteResponse:
    # BrokenWikilinkError/TemporalMetadataError are ValueError subclasses with their own
    # app-level handlers (api/errors.py) -- letting them propagate rather than catching
    # ValueError here keeps them mapped to their specific codes instead of ALREADY_EXISTS.
    try:
        result = await run_sync(
            note_create_service.save,
            workspace,
            body.title,
            body.content,
            body.tags,
            folder=body.folder,
            occurred_at=body.occurred_at,
            period=body.period,
        )
    except FileExistsError:
        raise HTTPException(status_code=409, detail=NoteError.ALREADY_EXISTS) from None
    return CreateNoteResponse(note_id=result["note_id"], warnings=result["warnings"])
```

- **Body**: a plain parameter typed as a Pydantic model (`body: CreateNoteRequest`), never
  `request: Request` + `await request.json()`. FastAPI validates shape, required fields,
  and types before the route body runs; a non-object payload (e.g. a JSON array) 422s
  automatically. REST request models rely on Pydantic's default `extra="ignore"` behavior
  (`CreateNoteRequest`/`UpdateNoteRequest` spell it out via `ConfigDict` as the policy
  record) -- MCP's `ToolInput` overrides it to `extra="forbid"` instead (a typo is a bug
  for an LLM caller, not for a REST client tolerating one extra field). Keep a field that
  must 422 the whole request when absent (`title`, `path`) declared with no default --
  Pydantic (and therefore the generated OpenAPI schema) only marks a field optional when it
  has one, so giving it a default to unify "missing" and "blank" handling would silently
  advertise the field as optional to every client.
- **Identity/target dependencies**: `Depends(get_required_user)` for a plain authenticated
  route, `Depends(resolve_workspace_target)` when the route needs one workspace,
  `Depends(resolve_note_target)` when the route addresses a `{note_id}` under a `{name}`
  workspace in the URL. The note-target resolver is what turns a note ID from a *different*
  workspace than the URL's into a 404 before any file access -- never reimplement that
  check with a manual `has_access`/`owner_id` comparison.
- **Service call**: one call into a `services/` method, blocking work always through
  `run_sync()` (see root `CLAUDE.md`'s concurrency rules). Routes do not touch
  repositories, the filesystem, or git directly -- see "Move filesystem/git work into the
  service" below.
- **Response**: construct the declared `response_model` type and return it, never
  `JSONResponse(some_dict)`. A service's return dict often carries fields the endpoint
  doesn't want to expose (`update()`'s `replaced` count is MCP-only) -- building the typed
  model is what keeps that filtering enforced instead of relying on every call site
  remembering to leave a key out.

## Error ownership: what's global, what stays local

`api/errors.py`'s `install_error_handlers` registers app-level handlers for
`RequestValidationError`, `InvalidFolderError`, `BrokenWikilinkError`,
`TemporalMetadataError`, and `GitError` (repository-level) -- every route gets these for
free and must not re-handle them locally. Concretely: don't write
`except InvalidFolderError: raise HTTPException(422, ...)` in a route: either don't catch it
at all, or -- if a broader `except ValueError` in the same `try` would otherwise swallow it
first, since all three are `ValueError` subclasses -- add a narrow pass-through clause
*above* that catch:

```python
except InvalidFolderError, TemporalMetadataError, BrokenWikilinkError:
    raise  # let the global handlers in api/errors.py map these
except FileExistsError:
    raise HTTPException(status_code=409, detail=NoteError.ALREADY_EXISTS) from None
except ValueError, FileNotFoundError:
    raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND) from None
```

`ValueError`, `FileNotFoundError`, and `FileExistsError` stay a local `except` per route,
because their meaning is genuinely route-specific (a bare `ValueError` means "not found" in
`update()`/`move()`/`delete()`, but a save-time `FileExistsError` means "already exists").
Don't add a blanket `except Exception` that maps every unexpected failure to a client-facing
code -- an unhandled exception should reach the global `_unexpected_exception_handler`
(500, generic body, `str(e)` only in the server log) rather than being mislabeled as a 4xx
or leaking exception text into the response.

A request-model field validator that must preserve a specific legacy error code (because the
frontend still keys UI copy off it) raises `pydantic_core.PydanticCustomError(type, msg)`
with a distinct `type`, and `api/errors.py`'s `_request_validation_handler` maps that type
back to the code. `CreateNoteRequest.title` and `CreateFolderRequest.path` do this for a
*present but blank* value. A required field's key can be missing outright though, and
Pydantic never runs a field validator against an absent required field -- that case
(`type="missing"`, plus `"string_type"`/`"string_too_short"`/`"literal_error"`/`"enum"` for a
present-but-wrong-shape value) is mapped by the request model itself instead: declare
`legacy_error_codes: ClassVar[dict[str, ErrorCode]] = {"field": SomeError.CODE}` on a
`RequestModel` subclass (`api/schemas/base.py`; see `CreateNoteRequest`/`MoveNoteRequest`/
`CreateFolderRequest`/`CreateSshKeyRequest`/`UpdatePreferencesRequest`). Every model owns
only its own fields' codes, so a new field named `title`/`path`/`folder`/... on an unrelated
model can never silently inherit another model's code the way one shared by-field-name table
could (#341) -- `RequestModel` also rejects at class-definition time a `legacy_error_codes`
key that isn't one of the model's own fields. For a nested body (a batch's `list[ItemModel]`
field), `_request_validation_handler` resolves the code from the *item* model, not the
wrapper. Check `frontend/src/lib/api/errors.ts` before adding a new entry to either
`legacy_error_codes` or `_CUSTOM_ERROR_TYPES`, and before deleting one if a field's
validation moves from a route into a schema.

## PATCH field-presence semantics

`UpdateNoteRequest`'s optional fields (`title`, `content`, `folder`, `tags`) don't need
`model_fields_set`/`exclude_unset` bookkeeping here: `NoteEditService.update()` already treats an
omitted keyword the same as an explicit `None` for each of these (its own default is
`None`), and Python's argument binding can't tell those two apart anyway -- both omitted-key
and explicit-`null` JSON parse to the same `None` attribute on the Pydantic model. Passing
`body.title` straight through reproduces exactly this "explicit null clears nothing, empty
string/list is a real value" contract.

`occurred_at`/`period` are the one exception: `update()` takes them through a separate
`_UNCHANGED` sentinel default (not `None`), because it must distinguish "the caller didn't
mention this field" (repair the value from the file on a read-modify-write) from "the caller
explicitly passed null" (today, also unchanged -- but a different code path reaches that
same outcome). Passing `occurred_at=body.occurred_at` directly would send an explicit `None`
whenever the field is omitted *or* null, defeating that sentinel. Route calls that update a
note must go through `workspace.temporal_kwargs(occurred_at, period)` and spread the result
(`**temporal_kwargs(...)`), which omits a key entirely when its value is `None` -- exactly
like the original hand-rolled `body.get(...)` call this migration replaced.

## Move filesystem/git work into the service

`api_create_folder` calls `NoteFolderService.create_folder(workspace, body.path)`
(`services/notes/folders.py`, injected via `get_note_folder_service`) -- the `.gitkeep`
marker-file write and git commit used to live in the route (`_create_folder_marker`). A
route function should be orchestration only: parse (via the typed body), authorize (via a
target dependency), call one service method, map the result. Any file/git-touching logic
belongs in `services/`, wrapped in the write-lock the service layer already uses
(`@target_write_transaction`/`@workspace_write_transaction`, see
`repositories/git.py` and `services/notes/CLAUDE.md`).

## Test setup

`tests/api/conftest.py`'s `build_test_app()` installs the same `install_error_handlers` as
production (`server.py`), so a route's error contract is identical in tests and prod. Use
the `auth_client`/`anon_client`/`no_access_client` fixtures for the three identity states
rather than constructing a `TestClient` by hand.
