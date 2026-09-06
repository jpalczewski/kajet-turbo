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
    `except` blocks for these same exceptions in `notes.py`/`folders.py`/`export.py`/
    `history.py` still shadow the global handler (a local `except` always wins) — remove
    those local blocks when migrating each route, don't do it as a separate pass.
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

- `embedding.py`, `ssh_keys.py` return **free-form exception text**
  as `error` (e.g. `{"error": "Profil nie istnieje."}`, `{"error": str(e)}`), not a
  machine-readable `ErrorCode`. `oauth.py` was migrated in #254: both `api_consent` and
  `api_pending_info` now raise `AuthError.PENDING_EXPIRED`. `jobs.py` was also migrated in
  #254: retry/dismiss now raise `JobError` codes instead of free text. `entries.py`'s
  folder/period validation error is also a literal string
  (`detail="period or folder is invalid"`). `frontend/src/lib/api/errors.ts` can't translate
  any of these — the frontend shows whatever server string arrives.
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

### Workspaces meta — `api/workspaces/workspace_meta.py`

CRUD on `/api/workspaces` and `/api/workspaces/{name}`. All bodies hand-parsed; `PATCH`
treats an explicit key as "set if the right type, ignore otherwise" (`isinstance` guards),
not true PATCH field-presence semantics. `has_access` checks already went through
`run_sync` before this phase.

### Workspace settings — `api/workspaces/workspace_settings.py`

Settings `GET`/`PATCH`, temporal-backfill `preview`/`apply`. `PATCH` iterates
`body["values"]` and 422s on non-dict; per-key `ValueError` from `set_setting` also 422s.
`apply_temporal_backfill` is the one route here on a typed Pydantic body
(`ApplyTemporalBackfillRequest`) already — a preview of the R2/R3 target shape.

### Export — `api/workspaces/export.py`

Single `GET .../export?format=`, returns `FileResponse` (zip/tar.zst/bundle) with a
`BackgroundTasks` cleanup callback. Correctly kept outside the JSON envelope per the
epic's own carve-out for file responses.

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

### SSH keys / embedding profiles — `ssh_keys.py`, `embedding.py`

Free-text error bodies throughout (see "Known inconsistencies"). SSH key creation and
embedding-profile create/update are correctly offloaded via `run_sync` already (RSA
keygen is CPU-bound; the embedding probe uses `asyncio.run()` internally and would deadlock
inline on the route's own loop).

### Preferences — `api/preferences.py`

The one route with genuine PATCH field-presence semantics (`if key in body`, not
`.get()`), because explicit `null` must 422 rather than no-op. Already on the shared
envelope (`PreferencesError.INVALID_INPUT`).

### Jobs — `api/jobs.py`

List/retry/dismiss, all sync `def` (unchanged — already off the event loop). `list` now
builds and returns `JobsResponse` directly (`response_model` enforced at runtime, no more
`JSONResponse({...})`) and takes `status` as a typed `str | None` query parameter instead
of reading `request.query_params` by hand. `retry`/`dismiss` now 404 with
`JobError.NOT_FOUND` and 200 with `OkResponse` instead of free-text bodies;
`JobRepository.retry`/`dismiss` collapse "no such job", "wrong owner", and "wrong status"
into one bool, so a single code is used rather than inventing a distinction the service
can't actually report.

### WebSocket — `api/ws.py`

Its own auth dependency, `_get_ws_user` — deliberately **not** `get_required_user` (a
WebSocket accept/close has different semantics than an HTTP 401), so it was untouched by
the `CurrentUser` migration. Still returns a raw `dict`.

## Manual `request.json()` sites (R2/R3 backlog for typed bodies)

None migrated in #247 — this enumerates the full blast radius for later phases.
`auth.py`/`oauth.py` were migrated in #254 (`api_login`, `api_consent`) and are removed
from this list; `api_pending_info` never had a body.

`ssh_keys.py:28`, `embedding.py:27,54`,
`preferences.py:33`, `workspace_settings.py:53`, `workspace_meta.py:44,85`,
`notes/crud/notes.py:72,125,158,225`, `notes/crud/folders.py:63`.

(`workspace_remote.py` fully migrated in #254 — removed from this list.)

## Frontend callers

The generated client (`frontend/src/lib/api/index.ts`, via `scripts/generate-api.sh` +
orval) covers every route above with a typed function per operation — it is current as of
this phase. Auditing which frontend call sites use a given operation is cheapest done at
migration time with `grep -rn "<generated-fn-name>" frontend/src`, immediately before
changing that route's contract, rather than enumerated here where it would go stale before
#253 even starts.
