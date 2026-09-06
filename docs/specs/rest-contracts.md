# REST contract inventory (R0, #247)

Snapshot of `src/kajet_turbo/api/` as of the #247 REST-infrastructure phase (epic #239).
Records what each route family does today so #253/#254 can migrate deliberately instead
of guessing at current behavior. Update the relevant section instead of trusting this file
once a family is migrated — it goes stale the moment code changes.

## Shared infrastructure delivered by #247

- **Identity**: `get_required_user`/`get_session_user` (`src/kajet_turbo/dependencies.py`)
  now resolve a typed `CurrentUser` (`id`, `email`, `timezone`, `locale`) instead of a bare
  `dict`. Provider names are unchanged, so `dependency_overrides` in tests still work.
- **Errors**: `src/kajet_turbo/api/errors.py`'s `install_error_handlers(app)` registers, on
  every app the project builds (`server.py`'s production apps and
  `tests/api/conftest.py::build_test_app`):
  - `HTTPException` → `{"error": <detail>}` if `detail` is a string/enum, or `<detail>`
    verbatim if it's already a dict (the existing per-route convention, e.g.
    `detail={"error": ..., "detail": ...}`).
  - `RequestValidationError` → 400 (malformed JSON, only reachable when the request sets
    `Content-Type: application/json` — FastAPI's `strict_content_type` default is why a
    JSON body sent without that header instead fails Pydantic validation at 422, not 400)
    or 422 (any other validation failure) with `{"error": "INVALID_INPUT", "detail": "<field
    path>: <message>"}`. **Currently dead code in production** — no route declares a typed
    Pydantic body model yet; every handler still hand-parses `await request.json()`. It
    activates route-by-route as #253/#254 adopt typed bodies.
  - `InvalidFolderError`, `BrokenWikilinkError`, `TemporalMetadataError`,
    `repositories.git.GitError` → consistent domain codes at the app level. Existing local
    `except` blocks for these same exceptions in `notes.py`/`folders.py`/`history.py` still
    shadow the global handler (a local `except` always wins) — remove those local blocks
    when migrating each route, don't do it as a separate pass. `export.py`'s local
    `except GitError` was removed in #254.
  - Any other `Exception` → 500 `{"error": "internal_error"}` plus a `logger.error`
    diagnostic line carrying `request_id` (now exposed via `request.state.request_id`,
    set by `LoggingMiddleware` in `src/kajet_turbo/log.py`) and `exc_type`; no request
    payload.
  - `RequestError.INVALID_INPUT` (`src/kajet_turbo/errors/request.py`) is a new generic
    code for validation failures that aren't owned by an existing domain enum. Added to
    `frontend/src/lib/api/errors.ts`'s `ERROR_MESSAGES`.
- **Blocking I/O**: `api_login`/`api_session_delete`/`api_sessions_delete`
  (`src/kajet_turbo/api/auth.py`) and every `async def` route that called
  `ws_service.has_access` directly now go through `run_sync()`. See "Blocking-call audit"
  below for the full before/after list.
- **Test harness**: `tests/api/conftest.py::build_test_app` installs the same handlers as
  production. Previously the test app was a bare `FastAPI()` with no exception handlers at
  all, so tests observed FastAPI's *default* envelope (`{"detail": ...}`) instead of the
  real one (`{"error": ...}` or a passthrough dict) — several existing tests asserted the
  wrong shape as a result and were fixed alongside this phase (`tests/api/test_notes.py`,
  `tests/api/test_preferences_endpoints.py`, `tests/api/test_reindex_endpoint.py`).

## Known inconsistencies to resolve during migration (not fixed in #247)

- `embedding.py`, `ssh_keys.py`, `oauth.py`, and `jobs.py` all previously returned
  **free-form exception text** as `error` (e.g. `{"error": "Profil nie istnieje."}`,
  `{"error": str(e)}`) instead of a machine-readable `ErrorCode` — all four were migrated
  in #254 (`EmbeddingProfileError`/`SshKeyError`, `AuthError.PENDING_EXPIRED`, `JobError`
  respectively). `entries.py`'s folder/period validation error is still a literal string
  (`detail="period or folder is invalid"`), not yet migrated. `frontend/src/lib/api/errors.ts`
  can't translate that one — the frontend shows whatever server string arrives.
- `UpdateNoteRequest` (`src/kajet_turbo/api/schemas/notes/crud.py`) does not declare
  `expected_sha`, which `api_update_note` (`notes.py`) reads from the raw body. #253 owns
  adding the field when it adopts the model.
- The `ErrorCode` union in `src/kajet_turbo/errors/__init__.py` is a PEP 695 `type` alias
  over several `StrEnum`s. Verified manually (`app.openapi()`) that it renders correctly as
  an `anyOf` of `$ref`s to each enum schema — no workaround needed. Nothing in the test
  suite pins this down yet; worth a small regression test in #255's OpenAPI-snapshot pass.
- `api/pending` (`oauth.py`) is the only REST route with no `get_required_user` dependency
  by design (pre-login OAuth consent screen needs it) — don't flag it as a missing-auth bug
  when auditing.

## Blocking-call audit (has_access / auth persistence)

Fixed in #247 — all now dispatch through `run_sync()`:

| File | Route(s) | Before | After |
|---|---|---|---|
| `api/auth.py` | `api_login` | `user_repo.get_by_email`, `verify_password`, `session_repo.create` called directly in `async def` | wrapped in `run_sync` |
| `api/auth.py` | `api_session_delete` | `session_repo.delete` direct | `run_sync` |
| `api/auth.py` | `api_sessions_delete` | `oauth_repo.delete_credentials_by_user`, `session_repo.delete_all_for_user` direct | `run_sync` |
| `api/workspace_remote.py` | `api_set_workspace_remote` (async) | `_guard` called `has_access` direct | new `_guard_async` wraps it in `run_sync` (superseded in #254 by `resolve_workspace_target`, which does its own blocking-safe access check); the three sync `def` routes keep using sync `_guard` (FastAPI's threadpool already covers them) |
| `api/workspaces/export.py` | `api_export_workspace` | direct | `run_sync` |
| `api/workspaces/workspace_settings.py` | all 4 routes | direct | `run_sync` |
| `api/workspaces/notes/crud/folders.py` | all 3 routes | direct | `run_sync` |
| `api/workspaces/workspace_meta.py` | `api_update_workspace`, `api_delete_workspace` | already used `run_sync` | unchanged (was already correct — the pattern these fixes copied) |

Deliberately **not** touched — sync `def` routes, already off the event loop by FastAPI's
own threadpool dispatch: `entries.py::api_entries_in`, `crud/tags.py::api_list_tags`,
`crud/reindex.py::api_reindex_workspace`.

Not audited in #247 (out of the has_access-specific scope this phase targeted, flagged for
#254's pass): `ws_service.workspace_path(...)` calls inside async routes
(`export.py`, `folders.py`). `provider.complete_authorization` in `auth.py`/`oauth.py` was
checked during #254's auth-family pass -- it's already `async def` and awaits its own
`run_sync()`-wrapped repository calls internally, so no route-level change was needed.

## Route families

### Auth / session — `api/auth.py`, `api/oauth.py` (migrated in #254)

| Route | Body | Null/omission | Status/envelope | Notes |
|---|---|---|---|---|
| `POST /api/login` | typed `LoginRequest{email, password, pending_id: str \| None}` | malformed JSON → 422/400 via the global `RequestValidationError` handler (`RequestError.INVALID_INPUT`); `pending_id` omitted skips the OAuth-completion branch entirely | 401 `AuthError.INVALID_CREDENTIALS`, 400 `AuthError.PENDING_EXPIRED` (no cookie set on this branch), 200 + `Set-Cookie` via an injected `Response` | timing-safe: `verify_password` always runs even for unknown email (`DUMMY_PASSWORD_HASH`) |
| `GET /api/session` | — | — | 200 `{email, preferences: {timezone, locale}}` | needs auth |
| `DELETE /api/session` | — | — | 200 always (idempotent even with no cookie); cookie deleted via an injected `Response` | |
| `DELETE /api/sessions` | — | — | 200; revokes all sessions + OAuth grants for the caller; cookie deleted via an injected `Response` | logs `user_signed_out_everywhere` |
| `POST /api/consent` | typed `ConsentRequest{pending_id: str}` | missing key → 422 `RequestError.INVALID_INPUT`; a present-but-invalid `pending_id` reaches `provider.complete_authorization` and raises there | 400 `AuthError.PENDING_EXPIRED`, 200 | |
| `GET /api/pending` | query param `id` | — | 404 `AuthError.PENDING_EXPIRED` (reused: same "unknown/expired pending_id" condition as `/api/consent`), 200 | **no auth dependency** — intentional; exempt from the typed-body migration (no body, protocol-adjacent) |

### Workspaces meta — `api/workspaces/workspace_meta.py` (migrated, #254)

CRUD on `/api/workspaces` and `/api/workspaces/{name}`. `POST /api/workspaces` takes a
typed `CreateWorkspaceRequest` (`name` required/non-blank; unlike `CreateNoteRequest.title`,
a *missing* `name` key is caught by a `model_validator(mode="before")` rather than left to
api/errors.py's global by-field-name `_REQUIRED_FIELD_CODES` table -- `name` is too common a
field name across the app's request models to key that table on safely, as
`tests/api/test_error_handlers.py`'s unrelated generic probe body demonstrated;
`description`/`folder`/`tags` optional); it has no `resolve_workspace_target` dependency
since it doesn't address an existing workspace yet.
`PATCH /api/workspaces/{name}` and `DELETE /api/workspaces/{name}` both use
`Depends(resolve_workspace_target)` instead of a hand-rolled `has_access` call. `PATCH`
takes `UpdateWorkspaceRequest` and applies it via `body.model_dump(exclude_unset=True)` --
`exclude_unset` is Pydantic's own `model_fields_set`-based mechanism (it dumps only the
fields present in `__pydantic_fields_set__`), so this *is* the field-presence semantics the
issue asks for, not an approximation of it -- real field-presence semantics, replacing the
old "set if the right type, ignore otherwise" `isinstance` guards. Behavior change: a
wrong-typed key the client did send (e.g.
`tags: "x"` instead of a list) now 422s instead of being silently dropped; an omitted key
or an explicit `null` are still both a no-op, matching `set_meta`'s existing
COALESCE-based "None leaves the column unchanged" contract.

### Workspace settings — `api/workspaces/workspace_settings.py` (migrated, #254)

Settings `GET`/`PATCH`, temporal-backfill `preview`/`apply` — all four now use
`Depends(resolve_workspace_target)`, and the two temporal-backfill routes take
`str(workspace.path)` instead of a separate `ws_service.workspace_path(...)` call.
`PATCH`'s body is `UpdateWorkspaceSettingsRequest{values: UpdateWorkspaceSettingsValues}`,
one optional `StrictBool` field per `workspace_settings.REGISTRY` key; only keys present in
`body.values.model_dump(exclude_unset=True)` are applied, matching the previous
per-key-in-`values`-dict behavior. Unlike the rest of this family's REST bodies,
`UpdateWorkspaceSettingsValues` sets `extra="forbid"` (an unknown setting key still 422s,
as before) and uses `StrictBool` rather than `bool` (a JSON string like `"yes"` still 422s
rather than being coerced) -- both preserve pre-#254 behavior that a plain `dict` body
happened to give for free. `apply_temporal_backfill` was already on a typed Pydantic body
(`ApplyTemporalBackfillRequest`) before this phase; besides the target dependency, its
error mapping was tightened too -- the service now raises `BackfillStaleError` (a
`ValueError` subclass) specifically for a stale preview batch, so the route can 409
`WORKSPACE_BACKFILL_STALE` for that case while every other `ValueError` (malformed
candidate shape: empty batch, blank/duplicate `note_id`) 422s `WORKSPACE_INVALID_INPUT`,
instead of every failure surfacing as a 409 with the raw exception string as `detail`.

### Export — `api/workspaces/export.py` (migrated, #254)

Single `GET .../export?format=`, returns `FileResponse` (zip/tar.zst/bundle) with a
`BackgroundTasks` cleanup callback. Explicitly exempt from `response_model` / the JSON
envelope per the epic's own carve-out for file responses; `format` stays a plain
`Literal[...]` query param (FastAPI already 422s an unknown value, so it gets no separate
request model). Gained `Depends(resolve_workspace_target)` for access-check parity with the
other two files in this family, replacing a direct `ws_service.has_access` call and a
separate `ws_service.workspace_path(...)` call. The local `except GitError` that used to
shadow `api/errors.py`'s global handler (and leaked `str(e)` into the 500 body) was removed
rather than migrated, per the "Shared infrastructure" note on `GitError` shadowing above.

### Notes CRUD — `api/workspaces/notes/crud/{notes,folders,contents,entries,tags,reindex}.py`

The bulk of the manual-`request.json()` surface (see list below). `notes.py` has the
richest error mapping (broken wikilinks, stale sha, folder validation, git errors) and is
the template #253 should follow when adding `CreateNoteRequest`/`UpdateNoteRequest` as
real body models. `folders.py`'s `_create_folder_marker` does a git commit synchronously
inside `run_sync` — correct today, but couples folder creation to git in the route instead
of a service (epic explicitly calls this out as the marker-file/Git logic to relocate).

### Notes content (read-only) — `api/workspaces/notes/content.py`, `history.py`

All `GET`, all sync `def`, uniform 404 → `NoteError.NOT_FOUND`. `history.py`'s
`api_restore_note_version` is the one `async def` here; it goes through
`resolve_note_target` for access, not a direct `has_access` call, so it was not part of
the blocking-call audit.

### Workspace remotes — `workspace_remote.py` (migrated, #254)

All four routes (`GET`/`PUT`/`DELETE .../remote`, `POST .../remote/push`) now follow the
#253 typed-endpoint pattern: `_guard`/`_guard_async` are gone, replaced by
`workspace: WorkspaceTarget = Depends(resolve_workspace_target)`. This is a deliberate
wire-format change — a 403 body used to be free-text `{"error": "Brak dostępu."}` and is
now the shared envelope `{"error": "ACCESS_DENIED"}`. `PUT` takes a typed
`SetWorkspaceRemoteRequest` (`origin_url`, `ssh_key_id` both `min_length=1`, `enabled`
defaults `True`); a present-but-blank field now 422s declaratively instead of the service
raising `ValueError("origin_url is required")`. The service still rejects a non-SSH
`origin_url` and an unknown `ssh_key_id` itself (domain checks a Pydantic validator can't
make — URL-scheme shape and a DB lookup), mapped locally to
`WorkspaceRemoteError.INVALID_INPUT` (400, `{"error": ..., "detail": <message>}`).
`DELETE`'s and `POST .../push`'s free-text 404/400 bodies ("Not found", "No enabled remote
configured") became `WorkspaceRemoteError.NOT_FOUND` / `NOT_CONFIGURED`
(`src/kajet_turbo/errors/workspace_remote.py`). Responses are the existing
`WorkspaceRemoteResponse`/`OkResponse` models, actually constructed and returned now
instead of being declared as `response_model` while the route returned a raw
`JSONResponse` (so FastAPI's response validation never ran pre-migration).

### SSH keys / embedding profiles — `ssh_keys.py`, `embedding.py` (migrated in #254)

`ssh_keys.py` and `embedding.py` were migrated in #254 (secrets-and-prefs): both take a
typed `CreateXRequest`/`UpdateXRequest` body and answer with `SshKeyError`/
`EmbeddingProfileError` codes instead of `{"error": str(e)}`. `CreateSshKeyRequest.algorithm`
is a `Literal` mirroring `crypto.ssh_keys.ALGORITHMS` (kept in sync by a test, since a type
checker won't accept that module's constants as `Literal` arguments), so an unknown
algorithm now 422s from Pydantic instead of the route's old 400. `EmbeddingProfileService`'s
repository raises a dedicated `ProfileNotFoundError` (a `ValueError` subclass) for a missing
profile in `update`/`set_active`, so the route distinguishes "not found" (404) from a probe
failure (400) by exception type instead of string-matching the message. SSH key creation and
embedding-profile create/update are correctly offloaded via `run_sync` already (RSA
keygen is CPU-bound; the embedding probe uses `asyncio.run()` internally and would deadlock
inline on the route's own loop). The private key never leaves `SshKeyService._view`, and
`api_key` is never included in a `ValueError` message, so neither reaches an error response.
Every route, including the `GET` list routes, constructs its declared `response_model`
directly (`SshKeysResponse`/`EmbeddingProfilesResponse` wrapping a list of
`SshKeyItem`/`EmbeddingProfileItem`) instead of returning a raw `JSONResponse` — the same
defense-in-depth filtering the `POST`/`PUT` routes already relied on now also covers list.

### Preferences — `api/preferences.py` (migrated in #254)

`PATCH` now takes a typed `UpdatePreferencesRequest` body (`timezone`,
`locale: Locale | None`) instead of `await request.json()`, using `model_fields_set` for the
same field-presence semantics it already had by hand (an omitted key is a no-op, an explicit
`null` still 422s — Pydantic can't tell those two apart from the resolved value alone).
`locale` being typed as the closed `Locale` enum means an unsupported value now 422s from
Pydantic before the route runs; `api/errors.py`'s `_request_validation_handler` maps that
`"enum"` failure on the `locale` field back to the pre-existing `PREFERENCES_INVALID_INPUT`
code so the client-visible contract is unchanged. `timezone` stays a plain `str` field --
`is_valid_timezone` needs the live IANA database, not a fixed enum -- so an unknown timezone
still 422s via the service's `ValueError`. `GET` returns the `PreferencesService`'s typed
`UserPreferences` directly (the service already builds that model in `_view`), so there is
no raw `JSONResponse` here either.

### Jobs — `api/jobs.py` (migrated in #254)

List/retry/dismiss, all sync `def` (unchanged — already off the event loop). `list` now
builds and returns `JobsResponse` directly (`response_model` enforced at runtime, no more
`JSONResponse({...})`) and takes `status` as a typed `str | None` query parameter instead
of reading `request.query_params` by hand. `retry`/`dismiss` now 404 with
`JobError.NOT_FOUND` and 200 with `OkResponse` instead of free-text bodies;
`JobRepository.retry`/`dismiss` collapse "no such job", "wrong owner", and "wrong status"
into one bool, so a single code is used rather than inventing a distinction the service
can't actually report.

### WebSocket — `api/ws.py` (assessed in #254, no change)

Its own auth dependency, `_get_ws_user` — deliberately **not** `get_required_user` (a
WebSocket accept/close has different semantics than an HTTP 401), so it was untouched by
the `CurrentUser` migration. Still returns a raw `dict`. #254 re-examined this file for the
"identity dependency reuse" the epic called out: `_get_ws_user` already resolves its
`SessionRepository` via the same `Depends(get_session_repo)` provider every HTTP route uses,
and calls the same `identity.resolve_session_user_from_cookies` primitive
`get_session_user`/`get_required_user` wrap — that sharing predates #247/#253 (commit
22f5529, #70). Swapping in `get_session_user`/`get_required_user` directly would be a
regression, not a cleanup: the former reads `app.state.resources` directly instead of going
through the `Depends` override the WS test app installs, and the latter's DB call is
synchronous and would block this route's event loop without a `run_sync()` wrapper it
doesn't have. No code change was made.

## Manual `request.json()` sites (R2/R3 backlog for typed bodies)

None migrated in #247 — this enumerates the full blast radius for later phases. `auth.py`,
`oauth.py`, `workspace_remote.py`, `ssh_keys.py`, `embedding.py`, `preferences.py`,
`workspace_settings.py`, and `workspace_meta.py` were all migrated in #254 and are removed
from this list; `api_pending_info` never had a body. Only the notes CRUD family remains:

`notes/crud/notes.py:72,125,158,225`, `notes/crud/folders.py:63`.

## Frontend callers

The generated client (`frontend/src/lib/api/index.ts`, via `scripts/generate-api.sh` +
orval) covers every route above with a typed function per operation — it is current as of
this phase. Auditing which frontend call sites use a given operation is cheapest done at
migration time with `grep -rn "<generated-fn-name>" frontend/src`, immediately before
changing that route's contract, rather than enumerated here where it would go stale before
#253 even starts.
