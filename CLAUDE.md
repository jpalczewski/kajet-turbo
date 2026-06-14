# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Backend (Python, managed with **uv**):
- Run server: `MCP_BASE_URL=http://localhost:8000 uv run kajet-turbo` (`MCP_BASE_URL` is required — used for OAuth redirect_uri)
- Tests: `uv run pytest` (single test: `uv run pytest tests/services/test_notes.py::test_name`)
- Lint/format: `uv run ruff check --fix . && uv run ruff format .`
- Type check: `uv run ty check` (ty is pre-1.0; suppress false positives with `# ty: ignore[rule]` + reason)

Frontend (SvelteKit in `frontend/`, managed with **bun** — not npm):
- Dev server: `bun run dev` (proxies `/api`, `/mcp`, `/authorize`, `/token` to localhost:8000)
- Type check: `bun run check` (svelte-check)
- Lint/format: `bun run lint` / `bun run format`

After changing backend API endpoints or models, regenerate the frontend client:
`bash scripts/generate-api.sh` (exports OpenAPI schema, runs orval → `frontend/src/lib/api/`)

## Schema changes

Use Alembic migrations, never `create_all`:
`uv run alembic revision --autogenerate -m "..."` then `uv run alembic upgrade head`.
Docker entrypoint runs `alembic upgrade head` automatically on start.

## Free-threaded Python 3.14t

The runtime is free-threaded Python (no GIL). Two hard rules:
- `DISABLE_SQLALCHEMY_CEXT_RUNTIME=1` must be set before any sqlalchemy import — it's done in `src/kajet_turbo/__init__.py` and `tests/conftest.py`. Never remove it: SQLAlchemy's C extensions don't declare free-threading support and silently re-enable the GIL.
- All blocking work (DB, git via dulwich, file I/O) in async endpoints and MCP tools must go through `run_sync()` from `src/kajet_turbo/concurrency.py` — never call sync repositories directly from async code.

Git mutations are serialized per workspace by two layers in `src/kajet_turbo/repositories/git.py`: an in-process `threading.Lock` (keyed by workspace path) plus a **cross-process** `fcntl.flock` on `<workspace>/.git/kajet-write.lock`. The flock is what keeps concurrent writes safe across the separate `kajet-api`/`kajet-mcp` processes (and any multi-worker setup) sharing the `/workspaces` volume — without it `dulwich` commits race and a commit can be silently lost (ref last-writer-wins). The cache is per-process: workspace writes bump the cache epoch (`src/kajet_turbo/cache.py`) within that process, and TTL bounds cross-process staleness.

## Conventions

- Commit messages use prefixes: `feat:`, `fix:`, `refactor:`, `bench:`, `style:`, `docs:`.
- Code comments in English only. Test data in English too — except occasional dedicated unicode/diacritics cases (e.g. "zażółć gęślą jaźń") where non-ASCII input is the point of the test.
- Each user workspace is a git repo (dulwich, pure Python) — file changes are committed with messages like `note: add <Title>`.
