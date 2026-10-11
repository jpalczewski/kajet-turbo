---
name: verify
description: Run kajet-turbo locally against a temp DB and a fake embedding endpoint, then drive the REST surface (login, notes, jobs, reindex) to observe changes end-to-end.
---

# Verifying kajet-turbo at runtime

## Launch recipe (temp everything, no prod deps)

Ports are never fixed: several sessions may run `/verify` at once from
different worktrees, so both servers bind free ports and every later command
uses `$APP` / `$EMBED_PORT`.

```bash
SCRATCH=$(mktemp -d)
# Fake OpenAI-compatible embeddings endpoint next to this file: deterministic
# vectors, ~150ms artificial delay, binds a free port and prints it first.
python3 .claude/skills/verify/fake_embed.py > $SCRATCH/embed.port &
EMBED_PID=$!
until [ -s $SCRATCH/embed.port ]; do sleep 0.1; done
EMBED_PORT=$(head -1 $SCRATCH/embed.port)

APP_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
APP=http://localhost:$APP_PORT

DB_PATH=$SCRATCH/kajet.db WORKSPACES_DIR=$SCRATCH/ws \
SECRET_KEY=verify-secret-key-0123456789abcdef \
KAJET_ADMIN_EMAIL=admin@test.local KAJET_ADMIN_PASSWORD=verify-pass \
MCP_BASE_URL=$APP MCP_PORT=$APP_PORT \
KAJET_WORKER_POLL_INTERVAL=0.25 \
uv run kajet-turbo > $SCRATCH/app.log 2>&1 &
APP_PID=$!
until curl -sf $APP/readyz > /dev/null; do sleep 0.2; done
```

Shell variables, including the random `$SCRATCH` path, do not survive between
Bash calls. Run launch, the requests below, and cleanup in a single Bash call,
ending with `kill $APP_PID $EMBED_PID`.

Role `all` runs an in-process worker thread (job queue drains without a
separate worker process). `KAJET_WORKER_POLL_INTERVAL=0.25` makes deferred
jobs observable within ~1s.

## Driving the REST surface

```bash
curl -c cookies.txt -X POST $APP/api/login -d '{"email":...,"password":...}'
curl -b cookies.txt -X POST $APP/api/workspaces -d '{"name":"notes"}'
# keyless profile → probes the endpoint, captures dim, activates:
curl -b cookies.txt -X POST $APP/api/me/embedding-profiles \
  -d "{\"name\":\"fake\",\"base_url\":\"http://127.0.0.1:$EMBED_PORT/v1\",\"model\":\"fake-8\"}"
curl -b cookies.txt -X POST $APP/api/workspaces/notes/notes \
  -d '{"title":"T","content":"# T\n\nbody\n"}'
```

Gotchas:
- PATCH/DELETE on notes require `expected_sha`; get it from
  `GET /api/workspaces/{ws}/notes/{id}/history` → `entries[0].sha`.
- Jobs API: `GET /api/me/jobs?kind=embed_note&status=pending`.
- There is NO REST search — search is MCP-only (`search_notes` tool), behind
  OAuth. Verifying search at runtime needs an MCP client with the OAuth dance.
- `sqlite3` CLI cannot read `note_chunks_vec_*` (vec0 virtual table); use
  `uv run python` with `sqlite_vec.load(conn)` to count vectors. Plain columns
  (`notes.index_state`, `note_chunks.dim`) read fine from sqlite3.
- Logs are JSONL in `$SCRATCH/app.log`; grep `worker_start`, `job_failed`,
  `vectors_attached`, `search_performed`.
