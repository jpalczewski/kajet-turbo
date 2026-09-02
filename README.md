# kajet-turbo

> [!WARNING]
> Highly unstable and unsafe. This is a solo hobby project driven by a love
> of refactoring and design more than of shipping — the git history is full
> of bugs, some still lurking. No security audit, no stability guarantees.
> Do not point it at data you can't afford to lose or expose.

> [!NOTE]
> That said, for personal use it's been reasonably solid operating on
> workspaces with 1k+ files.

MCP-first RAG for markdown notes — somewhere between [Serena](https://github.com/oraios/serena)
and Obsidian: from the MCP side you create a workspace (an Obsidian-style
vault), then add and edit files on it. The MCP endpoint is OAuth-capable,
so it can be exposed and connected to from an MCP client (Claude among
others).

The initial prototype was [kajet](https://github.com/jpalczewski/kajet), a
Rust-based predecessor to this project. The "turbo" rewrite happened for
the same kind of reason the original did: more dog walks, wanting to look
up notes from a phone on the go instead of a native macOS binary, and
being tired of a 50GB `target/` cache just to rebuild the thing.

Notes live as markdown files in a per-user git repo (workspace). The backend
chunks them, computes embeddings, and exposes hybrid search (SQLite FTS5 +
vectors) through:

- **MCP** (`/mcp`) — tools to search and edit notes, with OAuth, for
  plugging into any MCP client;
- **REST API** (`/api`) — for the SPA and integrations;
- **SPA** (SvelteKit, `frontend/`) — a human-facing note browser.

Three process roles (`KAJET_ROLE`) share the same SQLite database and the
same on-disk workspaces — see [Environment variables](#environment-variables)
below for topology details.

## Stack

**Backend:**
- Python 3.14 (free-threaded build — no GIL)
- [FastAPI](https://fastapi.tiangolo.com/) + [Starlette](https://www.starlette.io/) — REST API and ASGI app
- [FastMCP](https://github.com/jlowin/fastmcp) — MCP server, tools, and OAuth
- [SQLModel](https://sqlmodel.tiangolo.com/) + [SQLAlchemy](https://www.sqlalchemy.org/) + [sqlite-vec](https://github.com/asg017/sqlite-vec) — hybrid search: SQLite FTS5 + vector search in the same DB
- [Alembic](https://alembic.sqlalchemy.org/) — schema migrations
- [Dulwich](https://www.dulwich.io/) — pure-Python git, one repo per user workspace
- [Loguru](https://github.com/Delgan/loguru) — structured JSONL logging
- [python-frontmatter](https://github.com/eyeseast/python-frontmatter) + [markdown-it-py](https://github.com/executablebooks/markdown-it-py) — note parsing and chunking
- [Argon2-cffi](https://github.com/hynek/argon2-cffi) — password hashing
- [cryptography](https://cryptography.io/) — token/secret handling
- [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) — config from env vars

**Frontend:**
- [SvelteKit 5](https://svelte.dev/) + [Vite](https://vitejs.dev/) + TypeScript
- [Orval](https://orval.dev/) — generates the typed API client from the backend's OpenAPI schema
- [Vitest](https://vitest.dev/) — unit tests
- ESLint + Prettier + svelte-check — lint, format, type-check

## Local development

Backend (Python 3.14t, [uv](https://docs.astral.sh/uv/)):

```bash
uv sync
MCP_BASE_URL=http://localhost:8000 uv run kajet-turbo
```

Frontend (SvelteKit + [Bun](https://bun.sh/), proxies to `localhost:8000`):

```bash
cd frontend
bun install
bun run dev
```

Common commands:

```bash
uv run pytest                              # backend tests
uv run ruff check --fix . && uv run ruff format .
uv run ty check                            # type-checking
cd frontend && bun run check && bun run lint
bash scripts/generate-api.sh               # regenerate the API client after a backend change
```

Schema migrations use Alembic, never `create_all`; details in
[CLAUDE.md](CLAUDE.md#schema-changes). More on repo structure, concurrency
rules (free-threaded Python), and code conventions is also in
[CLAUDE.md](CLAUDE.md).

## Running (production / container)

```bash
uv sync
MCP_BASE_URL=http://localhost:8000 kajet-turbo
```

## Environment variables

### Required

| Variable | Description |
|---|---|
| `MCP_BASE_URL` | Public URL of the server (e.g. `https://kajet.example.com`). Some PaaS deployment targets set this automatically from their own env vars. |

### Server

| Variable | Default | Description |
|---|---|---|
| `MCP_HOST` | `0.0.0.0` | Listen address |
| `MCP_PORT` | `8000` | Listen port |
| `KAJET_ROLE` | `all` | Process role: `all` (MCP+API+SPA in one — dev), `mcp` (`/mcp` + OAuth only, **always 1 worker**), `api` (REST `/api` + SPA, N workers) |
| `MCP_WORKERS` | `1` | Worker count for role `all` |
| `API_WORKERS` | `2` | Worker count for role `api` |

Production topology (`docker-compose.yml`): ingress (Caddy) + `kajet-api`
(stateless, N workers) + `kajet-mcp` (stateful, 1 worker — MCP sessions and
`ctx.sample()` require a single process). Both roles share the `/data`
(SQLite) and `/workspaces` (git) volumes **on the same host**. The host proxy
only routes `Host → ingress:8000`; path splitting is done by the
`Caddyfile`.

## Development images

CI builds the final Dockerfile targets for `linux/amd64` and `linux/arm64`:

- `ghcr.io/jpalczewski/kajet-turbo-app`
- `ghcr.io/jpalczewski/kajet-turbo-ingress`

Pull requests only build, validate, and scan the images. A push to `main`
publishes images that passed the Trivy scan under the immutable
`sha-<commit>` tag. After successfully building both targets for both
architectures, CI also moves the convenient, moving `develop` tag.

> [!IMPORTANT]
> On first publish, set both packages to `Public` in their GitHub settings.
> The workflow checks anonymous pull access for both images before moving
> `develop`, so a private package will block the promotion; after fixing
> visibility you can safely re-run the same run.

```bash
# Latest known-good development build
docker pull ghcr.io/jpalczewski/kajet-turbo-app:develop
docker pull ghcr.io/jpalczewski/kajet-turbo-ingress:develop

# Reproducible pair of images from one commit
docker pull ghcr.io/jpalczewski/kajet-turbo-app:sha-<commit>
docker pull ghcr.io/jpalczewski/kajet-turbo-ingress:sha-<commit>
```

> [!NOTE]
> For deployments or debugging, use the same `sha-<commit>` tag for both
> images. GHCR does not update two packages transactionally, so `develop` is
> purely a shortcut for day-to-day work.

Every platform variant has a CycloneDX SBOM and provenance signed by GitHub
Actions. The multi-arch index attestation can be verified like this:

```bash
gh attestation verify \
  oci://ghcr.io/jpalczewski/kajet-turbo-app:sha-<commit> \
  -R jpalczewski/kajet-turbo
```

The SBOM is pinned to a specific platform manifest (`amd64` or `arm64`):

```bash
gh attestation verify \
  oci://ghcr.io/jpalczewski/kajet-turbo-app:sha-<commit>-arm64 \
  -R jpalczewski/kajet-turbo \
  --predicate-type https://cyclonedx.org/bom
```

Once a week, CI re-scans the published `develop` images to catch
vulnerabilities disclosed after publication. CI images are development
artifacts: the workflow does not trigger a deployment. The production
deployment builds both targets from the Dockerfile itself, independent of
these CI-published images.

> [!WARNING]
> Narrow exceptions to the Trivy gate live in `.trivyignore.yaml`. Each one
> is scoped to a specific package or path and has an expiry date; an
> exception without those constraints should not be added.

### Data

| Variable | Default | Description |
|---|---|---|
| `DB_PATH` | `/data/kajet.db` | Path to the SQLite database |
| `WORKSPACES_DIR` | `/workspaces` | Root directory for workspaces |

Vector embeddings are not configured via env vars. Each user sets their own
**embedding profile** — an OpenAI-compatible endpoint (`base_url` + `model`
+ dimension, optional API key, stored encrypted) — through the API/SPA.
Vector tables are sharded per dimension and created on demand, so nothing
needs to be pre-declared. Without an active profile, search still works —
it just falls back to FTS5 full-text only.

### Initialization

| Variable | Default | Description |
|---|---|---|
| `KAJET_ADMIN_EMAIL` | — | Admin account email (created on first start) |
| `KAJET_ADMIN_PASSWORD` | — | Admin account password |

### Logging

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_SQL` | — | Set to any value to log SQL queries (`LOG_SQL=1`) |

Logs are emitted to stderr as JSONL. Examples:

```bash
# production — INFO only, no SQL
kajet-turbo

# debug — full application logs
LOG_LEVEL=DEBUG kajet-turbo

# trace SQL queries
LOG_SQL=1 kajet-turbo

# full debug with SQL
LOG_LEVEL=DEBUG LOG_SQL=1 kajet-turbo 2> debug.jsonl
```
