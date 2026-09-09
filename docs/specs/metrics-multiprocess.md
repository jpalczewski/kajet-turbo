# Multiprocess metrics lifecycle — decision record

Companion to [`metrics.md`](metrics.md), covering what #310 had to establish before #311
can own registries and serve `/metrics`: how `prometheus_client`'s multiprocess mode
behaves under uvicorn's worker supervisor, and where the dead-child reaper runs.

Every claim below is measured, not reasoned about. The harness is
[`scripts/spike_multiprocess/`](../../scripts/spike_multiprocess/) — 21 assertions,
all passing on Linux/amd64 against Python 3.14.7 free-threaded and `prometheus_client`
0.26.0, which is the deployed runtime.

---

## 1. Reaper placement: on the exposition path

`MultiProcessCollector.collect()` begins with

```python
files = glob.glob(os.path.join(self._path, "*.db"))
```

on **every** scrape. The directory scan a reaper needs is therefore already paid for;
reusing it costs one `os.kill(pid, 0)` per file, over a directory holding a handful of
files for two children.

That removes the only real argument for the alternatives. A background thread buys
cleanup independent of scrape traffic, but a container nobody scrapes has no consumer for
the gauges being cleaned — and it costs a thread per process and another shutdown path in
an application whose lifespan ordering is already load-bearing. Running both solves a
problem that does not arise.

**Decision: sweep on the exposition path, before building the `MultiProcessCollector`
response.** Scan `PROMETHEUS_MULTIPROC_DIR` for `*_<pid>.db`, probe each PID with
`os.kill(pid, 0)`, and call `multiprocess.mark_process_dead(pid)` on `ProcessLookupError`.
Containers share a PID namespace, so the probe is meaningful.

## 2. What the reaper does and does not clean

`mark_process_dead` removes only gauge files whose mode begins with `live`:

```python
_LIVE_GAUGE_MULTIPROCESS_MODES = {m for m in Gauge._MULTIPROC_MODES if m.startswith("live")}
```

→ `liveall`, `livemax`, `livemin`, `livemostrecent`, `livesum`. Counters and histograms
are untouched and survive a child restart, which is the required behaviour.

Measured, killing a child with `SIGKILL` and letting uvicorn respawn it:

| | before kill | after kill, before reap | after reap |
| --- | --- | --- | --- |
| `livesum` gauge (each child contributes 1) | 2 | **3** | 2 |
| counter total | 60 | 60 | 70 (kept climbing) |
| histogram `_count` | 60 | 60 | 70 |

The middle column is the residue this issue exists for: a dead child keeps contributing to
live-state gauges until something reaps it, so pool checkouts, limiter borrowers and
in-flight counts read high for exactly as long as nothing sweeps.

## 3. Shared-state snapshot gauges must be `livemostrecent`, not `mostrecent`

This corrects a decision in #280 and in `metrics.md` §1.5.

Both epics chose `mostrecent` for shared-state snapshots (WAL size, queue depth, sampler
freshness) as defence in depth: those gauges live on roles that never run multiprocess
mode, so the setting is inert, but declaring it prevents an unset mode from silently
falling back to the forbidden `all` if `KAJET_METRICS_SAMPLE_SHARED` were ever set on
`api` or `mcp` by mistake.

In exactly that misconfiguration, `mostrecent` fails in the worst available way. It is not
in the `live*` set, so the reaper never removes it. Measured — one child stamps a snapshot
value, is killed, and the reaper runs:

| gauge mode | value before kill | value after reap |
| --- | --- | --- |
| `mostrecent` | 1990 (from the child about to die) | **1990 — the dead child still owns it** |
| `livemostrecent` | 1990 | 1780 (a live child's value) |

A dead process's WAL size or queue depth can remain the newest value indefinitely. The
metric stays plausible and stops changing, which is precisely the "stale sampling that
looks healthy" the milestone's *Done when* forbids.

**`livemostrecent` gives the same protection and fails clean**: the file is reaped, the
value disappears, and absence is visible where a frozen number is not. It is equally inert
on `worker` and single-process `all`, so nothing is given up.

## 4. Directory ownership

The supervisor creates `PROMETHEUS_MULTIPROC_DIR` and clears it **once**, before
`uvicorn.run`. Children never clear it. It never lives in the application DB volume.

Measured: starting over a directory still holding a previous run's files, the counter
restarts from zero rather than resuming a previous process's totals.

`PROMETHEUS_MULTIPROC_DIR` must be in the environment before anything imports
`prometheus_client` — `values.ValueClass` resolves the storage backend once, at import.
#280 places this in `src/kajet_turbo/__init__.py` beside the existing
`DISABLE_SQLALCHEMY_CEXT_RUNTIME` guard, gated on `KAJET_ROLE`.

## 5. Series names are stable across worker counts — with one caveat

`MCP_WORKERS=1` and `MCP_WORKERS=2` expose identical series names, and no `pid` label
appears in either, including under 24 concurrent scrapes, which also never duplicated a
counter.

The caveat is that worker count is not what selects the storage backend — the presence of
`PROMETHEUS_MULTIPROC_DIR` is, and #280 gates that on `KAJET_ROLE`. The modes are not
equivalent:

| | series for one counter and one histogram |
| --- | --- |
| single-process | `_total`, **`_created`**, `_bucket`, `_count`, `_sum`, **`_created`** |
| multiprocess | `_total`, `_bucket`, `_count`, `_sum` |

Multiprocess mode drops `_created`. **Tests never set `KAJET_ROLE`**, so they run
single-process and see series that `api` and `mcp` never emit. A test asserting a full
exposition set, or a dashboard written from test output, would describe something
production does not produce. Assert multiprocess exposition explicitly rather than
inferring it from a single-process run.

## 6. An unset gauge is absent, not zero

In multiprocess mode a gauge no process has ever `set()` writes no file and therefore
exposes **no series at all** — unlike single-process mode, where a `Gauge` is initialised
to 0 and always present.

This suits the milestone's rule that unknown data is never replaced with zero: a sampler
that has not yet run is legible as missing. Dashboards and alert rules must handle the
absent series rather than assuming a zero, and freshness alerting cannot rely on a gauge
existing.

## 7. Supervisor entrypoints must not re-enter under `forkserver`

Python 3.14 defaults to `forkserver` on Linux, where uvicorn's supervisor gains an extra
child process — the forkserver helper — alongside its workers. Anything counting the
supervisor's children sees N+1, which is a trap for a health check written against `pgrep`
rather than against which processes actually serve traffic.

More importantly, a worker re-imports the module that started the supervisor. A module
calling `uvicorn.run()` at import time re-enters it. **kajet is not exposed to this**:
uvicorn receives a factory *string*, so children import `kajet_turbo.server` and call the
factory rather than re-running `main()`. The constraint is on anything new that starts a
supervisor.

## 8. Deployed SQLite capability

`PRAGMA wal_checkpoint(NOOP)` is the only non-mutating way to read WAL frame state, and it
arrived in SQLite 3.51.0. The version kajet links is a property of `.python-version`, not
of the base image: python-build-standalone bundles its own SQLite.

Measured in the pinned base image against a throwaway WAL database holding 7 frames:

| mode | 3.14.3t / SQLite 3.50.4 | 3.14.7t / SQLite 3.53.1 |
| --- | --- | --- |
| `NOOP` | `(0, 7, 7)`, db 4096 → 8192 | `(0, 7, 0)`, db unchanged |
| `PASSIVE` | `(0, 7, 7)`, db 4096 → 8192 | `(0, 7, 7)`, db 4096 → 8192 |
| `ZZZ_NOT_A_REAL_MODE` | `(0, 7, 7)`, db 4096 → 8192 | `(0, 7, 7)`, db 4096 → 8192 |

The deliberately invalid mode is the discriminator: SQLite silently falls back to
`PASSIVE` for an unrecognised mode instead of rejecting it, so on 3.50.4 `NOOP` was
indistinguishable from garbage — which is what proves it was never a real mode there. The
database file growing is the checkpoint work it was doing.

`.python-version` moved to `3.14.7t` in #400, so the pragma is now genuinely available.
**The silent fallback survives that upgrade**, so the capability test must stay
behavioural: on a WAL holding `N` frames, a genuine `NOOP` returns `0` as its third
element while an unsupported mode returns `N` and has just checkpointed. Probe once at
sampler start and record the result as a capability; never derive it from
`sqlite3.sqlite_version`, because a base-image change can move it back and the failure is
silent — WAL metrics would keep reporting plausible numbers while every scrape
checkpointed.

If the probe ever comes back unsupported, frame count is still obtainable without SQLite:
the WAL format is a 32-byte header followed by frames of `24 + page_size` bytes, so `stat`
on the `-wal` file plus `PRAGMA page_size` gives size and frame count with no mutation.
Verified against the same probe database: `32 + 7 × (24 + 4096) = 28872`, matching the
measured file size exactly.

## 9. What is not settled here

The harness lives under `scripts/` rather than `tests/stress/` because
`prometheus_client` only becomes a project dependency in #311; it cannot run in CI until
then. Its assertions move into `tests/stress/test_mcp_multiprocess.py` with that issue,
against kajet's own app rather than a stand-in.

This record proves the mechanism. It does not prove kajet's integration with it — that
arrives with the registries and endpoints in #311.
