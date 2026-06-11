# Python 3.14t + async refactor — design

**Date:** 2026-06-11
**Status:** approved

## Goal

Fully exploit free-threaded Python 3.14t and correct async usage in the backend,
with performance measurements before and after. Secondary goal: learning value —
the project doubles as a free-threading/async playground.

## Background (research findings, June 2026)

- The current backend blocks the event loop: sync services/repositories (SQLModel
  sessions, dulwich git commits ~10–100 ms, frontmatter parsing) are called
  directly from `async def` REST endpoints and MCP tools.
- The whole dependency stack is free-threading ready: pydantic-core, SQLAlchemy
  (≥2.0.45), argon2-cffi-bindings, dulwich and uvloop ship `cp314t` wheels;
  FastAPI ≥0.136 tests 3.14t in CI; sqlite-vec is ABI-agnostic (pure-Python shim
  carrying a loadable SQLite extension). No dependency re-enables the GIL.
- **aiosqlite was evaluated and rejected.** It is sqlite3 wrapped in one thread
  per connection with a request queue — thread offloading with extra
  per-statement overhead, not true async I/O. Async SQLAlchemy additionally hard
  -requires greenlet, whose free-threading support is "initial" with a
  documented rare crash. The sync path avoids greenlet entirely.
- On 3.14t threads truly parallelize: FTS5 and sqlite-vec queries are CPU-bound
  C code that scales across threads; WAL gives N readers + 1 writer.
- FastMCP runs *sync* (`def`) tools in a threadpool automatically; our tools are
  `async def` and so bypass this safety net today.
- Expected costs: ~5–10 % single-threaded interpreter tax on 3.14t; uvicorn has
  no thread-based worker mode (processes only) — parallelism comes from the
  AnyIO/FastMCP threadpools inside one process.

## Decision: approach A — 3.14t + real threads, sync core

Services and repositories stay synchronous. All sync work invoked from async
contexts is dispatched to a bounded threadpool. No aiosqlite, no greenlet.

### 1. Runtime

- `.python-version` / Dockerfile: switch to CPython **3.14t** installed via uv
  (`uv python install cpython-3.14t`); keep the uv base image.
- Bump `sqlalchemy>=2.0.45` (first release with free-threading pool fixes).
- Log `sys._is_gil_enabled()` at server startup so a silently re-enabled GIL is
  visible in logs immediately.

### 2. Async/sync boundary (core of the refactor)

- New module `kajet_turbo/concurrency.py` exposing `run_sync(fn, *args)` =
  `anyio.to_thread.run_sync` with a **dedicated `CapacityLimiter(10)`**
  (= pool_size 5 + max_overflow 5), so DB/git work neither starves AnyIO's
  default 40-thread pool nor oversubscribes the SQLAlchemy connection pool.
- REST: existing sync `GET` endpoints (`def`) stay as they are (Starlette
  threadpool). Async write endpoints (`POST/PATCH/DELETE`) stop calling sync
  services directly — every service call goes through `run_sync`.
- MCP: all tools change from `async def` to **`def`** — FastMCP runs sync tools
  in its threadpool automatically. Minimal diff.
- Services and repositories remain synchronous — no `await`, no greenlet,
  no aiosqlite.

### 3. Thread safety

With real parallelism, shared state gets audited:

- Singletons from `dependencies.py` (services/repos) — verify they are
  stateless.
- **Git: per-workspace `threading.Lock`** (keyed lock in a dict) around
  commit/delete/rename — two parallel dulwich commits to the same repo are a
  real race. History reads stay lock-free.
- SQLite: WAL + `busy_timeout=5000` already configured; the connection pool
  serializes access to each connection.

### 4. Cache (in-process, must earn its keep)

- Thread-safe TTL cache (cachetools `TTLCache` + lock) with a **per-workspace
  epoch**: every save/delete/rename/reindex bumps the workspace's epoch
  counter, and the epoch is part of the cache key — invalidation is free and
  race-free.
- Candidates, in order of expected gain: search results (FTS + hybrid),
  markdown render / note read, git file history (`file_history` walks up to 50
  commits in pure-Python dulwich — expensive).
- Rule: each cache stays only if benchmarks show a meaningful win — ≥20 %
  improvement in p95 latency or RPS on the scenario it targets; otherwise it is
  removed (YAGNI).
- Known limitation (documented, accepted): the cache is per-process. It is
  designed for the current default of 1 worker; with `MCP_WORKERS>1` each
  process has its own cache and the in-memory epoch does not synchronize
  across processes.

### 5. Benchmarks (before and after)

- `scripts/bench.py`: httpx + asyncio against a running server, seeded
  workspace (~500 notes with realistic content).
- Scenarios: note read, note list, FTS search, hybrid search, note save (full
  git-commit path), 80/20 read/write mix at concurrency 1/10/50, and full
  workspace reindex (total time).
- Metrics: RPS, latency p50/p95/p99, process RSS. Output as JSON plus a
  markdown table committed under `docs/`.
- **Three-way matrix** to separate effects:
  1. baseline — current `main` on 3.14,
  2. refactored code on 3.14,
  3. refactored code on 3.14t.

  This isolates how much the async fix contributes vs free-threading, and
  whether the ~5–10 % single-thread tax of 3.14t hurts.

### 6. Tests and error handling

- Existing pytest suite must pass without semantic changes (exceptions
  propagate normally out of `to_thread`; `HTTPException` behaves as before).
- New concurrency test: parallel save + search + history against one workspace
  to catch git races and cache races.

## Expected outcome

- No event-loop freezes during writes/search (today every git commit blocks
  *all* requests for ~10–100 ms).
- Reads and search scale with cores on 3.14t.
- Lower p95/p99 under concurrency.

## Out of scope

- aiosqlite / async SQLAlchemy migration (rejected — see Background).
- Granian or other server swap (possible follow-up if benchmarks justify it).
- Cross-process cache coherence for `MCP_WORKERS>1`.
- Background task queue for reindexing.
