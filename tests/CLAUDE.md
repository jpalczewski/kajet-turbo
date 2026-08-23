# Tests

## Test at the cheapest layer that proves the thing

Pick the layer by what can actually go wrong, then prove the wiring above it **once**.

A rule that lives in a pure function belongs in a pure-function test: the whole of
`tests/markdown/test_note_edit.py` runs with every case under 5ms, so generating the entire
mode/parameter grid there is free. The same assertion at the MCP boundary costs ~2.3s per case — `mcp_server` is
function-scoped, so every case copies a DB, runs `build_mcp()`, inits a git workspace and
does a `save_note`/`get_note` round trip. One boundary case is enough to show the
`ValueError` → `ToolError` wiring holds; the rest of the matrix goes downstairs.

Never parametrize an expensive fixture over cases a fast test already covers.

## Reach for the existing fixture before writing a local one

- `tests/conftest.py` — `database` / `database_factory` (copies a migrated template, so no
  Alembic per test), `git_workspace_factory`, `note_file_factory`
- `tests/services/conftest.py` — `service`, `workspace`, `seed_user`, `build_note_service`,
  `build_workspace_service`
- `tests/mcp_tools/conftest.py` — `mcp_server` (seeded user `u1`, patched access token),
  `tokenless_mcp_server` (for auth-rejection tests), `workspaces_dir`
- `tests/mcp_tools/helpers.py` — `call_json` (never hand-roll
  `json.loads(result.content[0].text)`), `SHA_LIKE`

A helper needed by a second file moves to the suite's `helpers.py` — it does not get copied.
`_head_sha` exists three times across `tests/services/` and the save→`get_note`→sha dance
exists in several `tests/mcp_tools/` files; that is the failure mode this rule prevents.

## Tests run in parallel, against a fresh DB each

`addopts = "-q -n auto"`, so xdist is on by default and each worker gets its own `DB_PATH`
(see `pytest_configure` in `tests/conftest.py`). Consequences:

- no shared global state, no fixed paths outside `tmp_path`, no ordering assumptions
- a test that only passes when run alone is broken, not flaky
- debugging a single test: `uv run pytest -n0 -x "tests/area/test_x.py::test_name"` — xdist
  swallows output otherwise
- run the suite as bare `uv run pytest` (`testpaths` handles the rest). Passing several
  directories as arguments can drop the repo root off `sys.path`, and modules doing
  `from tests.services.conftest import ...` then fail collection with
  `ModuleNotFoundError: No module named 'tests'` — a path artifact, not a real breakage

`asyncio_mode = "auto"`, so async tests need no `@pytest.mark.asyncio`.

The env-var block at the top of `tests/conftest.py` runs before `kajet_turbo` is imported
(`DISABLE_SQLALCHEMY_CEXT_RUNTIME`, `DB_PATH`, `MCP_BASE_URL`) — `kajet_turbo.dependencies`
builds a `Database` at import time. The `E402` ignore for that file is deliberate; do not
tidy those imports upward.

## Tests are for finding bugs, not for certifying the code

A test written by reading the implementation and asserting what it currently does proves
nothing — it re-states the bug in a second file and goes green. Write the test that could
plausibly *fail*: derive it from the contract, then go looking for the input that breaks it.

Where the bugs actually live here: empty strings vs absent values, a parameter that is
legal in one mode and not another, two matches where the code assumed one, a stale sha, a
batch where item 3 is bad, concurrent writes to one workspace, a note title that is also a
folder path, and fixture data that is random — `grep` scans the raw file including the
frontmatter, whose note id is a 7-char nanoid, so a short alphanumeric needle collides with
it eventually. Pick test data that makes the collision impossible, not just unlikely. When a rule is a matrix, generate the grid instead of hand-picking cases — the
case you would not think to write by hand is exactly the one that catches the regression.

### Cover the refusal, not just the happy path

A feature is only described by its tests once they also pin down what it *refuses*. For
anything new, write the failure case in the same pass: the wrong or missing parameter, the
stale `expected_sha`, the note that is not there, the ambiguous anchor or heading, the
unauthenticated caller (`tokenless_mcp_server`), the batch item that sinks the batch.

A refusal test asserts two things, and the second is the one that catches real regressions:

1. the call failed, with the message naming *why*
2. **nothing changed** — re-read the note and assert the old content, tags and title stand

Point 2 is what separates "it raised" from "it refused". An all-or-nothing batch is not
proven by `applied is False`; it is proven by re-reading every note in the batch, the valid
items included, and finding them untouched.

## Assert the observable contract

Prefer the value a caller actually receives over a substring of it: `note["content"] ==
"Hello earth."` beats `"earth" in note.content[0].text`, which passes for the wrong reasons.

When asserting an error, match the part that carries the meaning — the parameter name, the
mode — not just the exception type. A bare `pytest.raises(ToolError)` will happily pass on a
completely different failure: a rename in this repo turned one such test green on
"Unexpected keyword argument" while the behaviour it claimed to cover went untested.

Every destructive note operation is gated on a fresh `expected_sha`, so a test that edits or
deletes must read the sha first. That is what the seed helpers are for.
