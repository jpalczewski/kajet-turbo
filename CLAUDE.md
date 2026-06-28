# Repository Guidelines

## Project Structure

- `src/kajet_turbo/`: Python backend plus MCP/API code.
- `tests/`: pytest suites grouped by area: `api/`, `services/`, `repositories/`, `mcp_tools/`, `markdown/`, `auth/`, and `stress/`.
- `frontend/`: SvelteKit app managed with Bun. `frontend/src/lib/api/` is generated from the backend OpenAPI schema.
- `alembic/versions/`: database migrations.
- `scripts/`: maintenance and code generation scripts.
- `docs/`: specs, benchmark notes, and project documentation.
- `ops/`: operational assets and logs.

Keep code close to its layer: route handlers call services, services call repositories, and handwritten UI code uses generated API clients without modifying them.

## Build, Test, and Development Commands

- `MCP_BASE_URL=http://localhost:8000 uv run kajet-turbo` starts the backend. `MCP_BASE_URL` is required for OAuth redirect URIs.
- `uv run pytest` runs backend tests. Use a node id for one test, for example `uv run pytest tests/services/test_notes.py::test_name`.
- `uv run ruff check --fix . && uv run ruff format .` lints and formats Python.
- `uv run ty check` type-checks Python. `ty` is pre-1.0; add `# ty: ignore[rule]` only with a reason.
- `cd frontend && bun run dev` starts SvelteKit and proxies backend routes to localhost:8000. Use Bun, not npm.
- `cd frontend && bun run check`, `bun run lint`, and `bun run format` check, lint, and format frontend code.
- `bash scripts/generate-api.sh` regenerates `frontend/src/lib/api/` after backend API or model changes.

Run commands from the repository root unless the command explicitly starts with `cd frontend`.

## Schema Changes

Use Alembic migrations, never `create_all`. Generate with `uv run alembic revision --autogenerate -m "..."`, review the file, then apply `uv run alembic upgrade head`. Docker also upgrades on start. Keep migrations focused.

Locally there is no `/data/kajet.db`. Use `scripts/migrate.sh` instead — it creates a throwaway SQLite file, builds a temp `alembic.ini`, and forwards all args to `alembic`:

```bash
bash scripts/migrate.sh revision -m "add foo table"   # autogenerate
bash scripts/migrate.sh                                # upgrade head
bash scripts/migrate.sh current                        # check revision
```

The generated file lands in `alembic/versions/` as usual. Review it, delete spurious `alter_column` noise (TEXT↔AutoString no-ops that SQLite doesn't need), then commit.

## Critical Runtime Rules

Runtime is free-threaded Python 3.14t, so blocking and C-extension behavior matters.

- `DISABLE_SQLALCHEMY_CEXT_RUNTIME=1` must be set before SQLAlchemy imports. See `src/kajet_turbo/__init__.py` and `tests/conftest.py`; do not remove or move this setup casually.
- Blocking DB, Dulwich git, and file I/O work in async endpoints and MCP tools must use `run_sync()` from `src/kajet_turbo/concurrency.py`.
- Each user workspace is a Dulwich git repo. Git writes use an in-process `threading.Lock` and a cross-process `fcntl.flock` on `<workspace>/.git/kajet-write.lock`.
- Workspace writes bump the per-process cache epoch in `src/kajet_turbo/cache.py`; TTL bounds cross-process staleness.

Do not bypass repository, cache, or locking helpers for convenience.

## Coding Style & Quality Preferences

- Prefer typed, explicit APIs and narrow data models. Avoid `Any` unless boundary data requires it, and keep that boundary small.
- Keep code elegant with clear names, small cohesive functions, and simple module boundaries.
- Design for extension where requirements are real. Avoid speculative abstractions, but leave obvious extension points when the domain already has variation.
- Follow established project patterns and framework best practices before adding dependencies, new styles, or new architectural conventions.
- Prefer structured parsing and typed helpers over ad hoc string manipulation.
- Keep errors actionable. Include enough context for debugging, but do not expose secrets.
- Comments should explain non-obvious decisions, concurrency constraints, or domain rules. Do not narrate obvious code.
- Write code comments and ordinary test data in English.
- Use non-ASCII test data only when Unicode behavior is the point.
- Do not hand-edit generated code. Regenerate it instead.
- User workspace file changes are committed with messages such as `note: add <Title>`.

## Frontend Guidelines

Keep Svelte components focused and typed. Put shared UI helpers under `frontend/src/lib/`. Use the generated API client instead of duplicating fetch logic. When backend contracts change, regenerate the client and update call sites together.

## Testing Guidelines

Use `uv run pytest` for backend tests. Place tests under `tests/<area>/` and name files `test_*.py`. Prefer focused tests near the affected layer: services in `tests/services/`, repositories in `tests/repositories/`, API behavior in `tests/api/`, and MCP tools in `tests/mcp_tools/`.

Cover behavior, edge cases, and regressions, especially for concurrency, persistence, authorization, generated API contracts, migrations, and user-visible workflows. For frontend changes, run `cd frontend && bun run check`.

## Commit & Pull Request Guidelines

Commit prefixes used in history include `feat:`, `fix:`, `refactor:`, `bench:`, `style:`, and `docs:`. Keep commits scoped and messages imperative when possible.

PRs should describe the change, list tests, link issues, and include UI screenshots. Mention migrations, generated API updates, runtime-rule changes, or compatibility risks.
