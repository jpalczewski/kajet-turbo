# Metric catalog, label domains and bucket contract

Normative catalog for the *Prometheus application metrics* milestone (#280). No code and
no dependency belongs here; #311 introduces the client and the facade.
Multiprocess lifecycle — reaper placement, directory ownership, and what does and does not
survive a child restart — is a companion record in
[`metrics-multiprocess.md`](metrics-multiprocess.md), measured under a real uvicorn
supervisor.

This document has two halves with different lifetimes:

- **Parts 1 and 2 — the contract** are fixed now. Naming, label discipline, label
  sourcing, aggregation rules, `multiprocess_mode` and the bucket sets. These are the
  decisions that break stored series once dashboards and alert rules exist, so they are
  settled before any of them is emitted.
- **Part 3 — the family table** is filled in per family. A family whose observation point
  already exists is entered now; one that a later issue creates (#315's search sub-phases,
  the `_embed` gap #316 closes, #312's sampler gauges) is entered by the pull request that
  creates it, against the rules in Parts 1 and 2. That keeps this document from having to
  design #312–#316 in advance and then drift when they land.

A reader implementing any single entered family should not need to open #280.

---

## Part 1 — Contract

### 1.1 Naming

- Every family starts with `kajet_`.
- Base units are **seconds** and **bytes**, never milliseconds. Log fields are named
  `*_ms` and stay that way; the facade divides by 1000 exactly once, at the observation
  point. Every bucket boundary in this document is in seconds.
- Duration families end `_seconds`, counters end `_total`, byte gauges end `_bytes`.
- HELP text is one sentence, states what is counted and what is excluded.

### 1.2 Label discipline

Restating #280 as an enforceable rule, because this catalog is what enforces it.

**Never** a user, session, request, job or note ID; a workspace name; a model or profile
name; a URL, query, payload, SQL text or exception message. Creating a note, a workspace
or a user must not add a series. Deployment and instance labels come from scrape
configuration, not from the application.

Every label has a **bounded value domain enumerated in this document**, plus a sentinel
for anything outside it.

### 1.3 Label sourcing — where the value comes from

The domain is only bounded if the value cannot be attacker- or bug-supplied. This is the
rule most easily lost during implementation, so it is stated per label with the sentinel
and whether that sentinel is actually reachable.

| label | source | sentinel | sentinel reachable? |
| --- | --- | --- | --- |
| `route` | the **matched** Starlette route's path template (`scope["route"].path`) | `<unmatched>` | Yes — unmatched traffic, including the root-mounted SPA |
| `method` | `request.method`, uppercase, against the HTTP verb set | `other` | Practically no; kept because the verb arrives from the wire |
| `tool` | the **registered tool name allowlist**, not `context.message.name` | `unknown` | Yes — `ToolDispatchMiddleware.on_call_tool` reads the requested name *before* FastMCP resolves it |
| `kind` | the job handler registry's keys | `unknown` | Yes — the `fail_terminal` path for a misrouted job |
| `operation` | `f"{repository_name}.{action}"`, both application constants | none needed | n/a — never caller-supplied |
| `phase` | the `PerfSpan` field vocabulary (§1.6) | none needed | n/a |
| `category` | the `run_sync` dispatch categories (§1.7) | `other` | Yes |

Two of these deserve emphasis:

**`route` must not be taken from `request.url.path`.** The correct mechanism already
exists and is deployed: `_http_route_fields` in `src/kajet_turbo/log.py:288` reads
`scope["route"].path` and collapses everything else to `<unmatched>` rather than falling
back to a URL that contains user-authored segments. Production logs confirm it works —
before it landed, the `path` field held hundreds of distinct raw values carrying
workspace names; since it landed, every value is a template or a sentinel. #314 reuses
this function's approach rather than inventing a second one.

**`tool` must not be taken from the request.** `ToolDispatchMiddleware.on_call_tool`
(`src/kajet_turbo/mcp/tooling.py:131`) receives `context.message.name` before resolution,
so a buggy or hostile client can present arbitrary strings. Unregistered names collapse to
`unknown`. This is the one place in the catalog where an unbounded label would be remotely
triggerable.

### 1.4 Outcome and status live on counters, never on histograms

`outcome`, `status` and `status_class` appear **only** on counter families. No histogram
carries them.

Rationale: it is the single rule with an order-of-magnitude effect on the budget
(≈3 200 series against ≈6 100 — Part 4), and "how long did the failure take" is a question
answered better from the structured log line, which already carries `error_type` and full
context. Alerting on error *rate* needs the counter; it does not need the failed request's
latency distribution.

This is a global rule so that #312–#316 cannot each decide it differently.

### 1.5 Gauges and `multiprocess_mode`

Every `Gauge` declares `multiprocess_mode` explicitly. The client default `all` is
**forbidden**: it emits one series per PID with a `pid` label, so cardinality grows on
every child restart.

| gauge class | mode | why |
| --- | --- | --- |
| Live state — pool checkouts, limiter borrowers, in-flight requests/tools/jobs | `livesum` | The deployment-wide value is the sum of what each live child currently holds |
| Shared-state snapshots — WAL size, page/freelist counts, queue depth, sampler freshness | `livemostrecent` | One owner samples them; the newest snapshot is the answer, and `live*` is the only family the dead-child reaper cleans |

Shared-state gauges declare a mode even though their owning roles (`worker`,
single-process `all`) never run multiprocess mode, so the setting is inert there. That is
deliberate: if `KAJET_METRICS_SAMPLE_SHARED` is ever set on an `api` or `mcp` process by
mistake, an unset mode silently falls back to the forbidden `all`. Declaring it costs
nothing and removes the only way that mistake stays quiet.

The mode is `livemostrecent`, not `mostrecent`, and the difference only shows up in
exactly that misconfiguration. `mark_process_dead` cleans only gauges whose mode begins
with `live`, so a plain `mostrecent` file written by a child that then dies is never
removed — a dead process's WAL size or queue depth can stay the newest value indefinitely,
plausible and frozen. `livemostrecent` is reaped instead, so the value disappears and
absence is visible where a stuck number is not. Measured in
[`metrics-multiprocess.md`](metrics-multiprocess.md) §3.

### 1.6 Phase vocabulary — adopt `PerfSpan`, do not invent

`src/kajet_turbo/perf.py` already defines the phase names that #315 and #316 want to
label. The catalog adopts that vocabulary; a second one must not appear.

```
chunk_ms   db_ms       embed_batches      embed_cache_hits  embed_cache_misses
embed_http_ms          fts_ms             git_lock_wait_ms  git_ms
index_superseded       limiter_wait_ms    meta_ms           reindex_note_skipped
reindex_note_superseded                   vec_ms            workspace_write_ms
```

The `phase` label value is the field name with its `_ms` suffix stripped (`git_lock_wait`,
`embed_http`, `fts`, `vec`). Counter-shaped fields (`embed_cache_hits`,
`index_superseded`, …) become counters, not histogram phases.

This vocabulary serves two consumers: the phase label here, and the span names in #394's
tracing epic. One dictionary, two readers.

### 1.7 `run_sync` dispatch categories

There are 117 `run_sync()` call sites. A per-site label is not admissible. The `category`
label domain is:

| value | dispatched work |
| --- | --- |
| `db` | repository and service calls whose cost is a SQLite transaction |
| `git` | Dulwich operations and workspace file writes under the git lock |
| `embed` | embedding provider calls and cache maintenance |
| `file` | workspace file I/O outside the git lock (export, read-through) |
| `other` | sentinel |

**Proposed, not settled**: the categories are derived from the call-site distribution
(`auth.py` 20 sites, the MCP note modules 33, the REST workspace modules 22, the rest
scattered). #315 owns confirming both the domain and the tagging mechanism — the callable
is passed positionally to `run_sync(fn, ...)`, so the category cannot be inferred reliably
and needs an explicit argument or a decorator.

### 1.8 Exposition constraints

Classic histograms only. No `Summary` — it cannot be aggregated across processes. No
native histograms. **No exemplars**, and the reason is specific rather than stylistic:
`prometheus_client`'s `MultiProcessValue.set_exemplar()` is a bare `return` and
`get_exemplar()` a bare `return None`, both marked
`# TODO: Implement exemplars for multiprocess mode.` in `values.py`. Multiprocess mode is
exactly the `api` and `mcp` roles, which are exactly where the route and tool histograms
live, so exemplars would be dropped silently where they are wanted and work only on
`worker` and single-process `all` — an asymmetry that reads like a partial-coverage bug.

The metric→trace bridge is therefore `trace_id` on the correlation log line, not an
exemplar. See #394.

### 1.9 Multiprocess aggregation is not a budget input

Under multiprocess mode, `N` children write per-PID files and the registry aggregates
them **at exposition**. A scrape of an `api` container with `API_WORKERS=2` returns one
series per label combination, not two. Per-PID files are a disk-lifecycle concern owned by
#310; they never multiply exposed series.

Related: `on_call_tool` observes tool invocations only. MCP protocol traffic —
`initialize`, `list_tools`, resource reads — appears solely as HTTP under the `/mcp` route
template.

---

## Part 2 — Bucket sets

Boundaries are the most expensive decision here: changing one breaks every stored series
in the family. Where production data exists, the set is derived from it; where it does
not, the set is designed for the alert threshold and says so.

The corpus is 16 803 structured log events from the production role logs
(`ops/logs/produkcja_*`, 2026-06 to 2026-09, single user, spanning several refactors).
Percentiles below are order-of-magnitude evidence, not authoritative SLOs.

### B1 — HTTP request duration · *derived* (n=2 294)

Observed: p50 3 ms · p75 11 ms · p90 29 ms · p95 55 ms · p99 497 ms · max 2.5 s.

```
0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5
```

### B2 — MCP tool call duration · *derived* (n=353)

Observed: p50 136 ms · p75 503 ms · p90 959 ms · p95 1.55 s · p99 2.79 s · max 4.4 s.

```
0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30
```

**A separate set from B1 is required, not a convenience.** #280 groups HTTP and tool work
as one "millisecond-to-second" scale, but their medians sit 45× apart: a shared set would
put most HTTP requests in the first bucket and most tool calls in the last few. Same series
cost, far better resolution.

### B3 — DB transaction duration · *derived, with a known blind spot* (n=294)

Observed on `repository_operation`: p50 3.1 ms · p75 5.7 ms · p90 17 ms · p95 30 ms ·
p99 164 ms · max 192 ms.

```
0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5
```

**Blind spot, stated deliberately.** `repository_operation` is emitted only for mutations
and operationally significant hits — hot-path reads, empty polls and cache reads are timed
but unlogged. The corpus is therefore write transactions including fsync, and the
sub-millisecond read floor is unobserved *by construction*. The set starts at 0.5 ms for
that reason. #312 covers three granularities (statement, session, transaction); only
transaction is informed by this corpus.

The per-operation aggregate `db_ms` (p90 190 ms, p95 827 ms) is a **different quantity** —
the sum of every transaction inside one HTTP or tool operation, not one transaction. It is
not a family in this catalog: #315 owns per-operation attribution, and if it enters a
family for that sum it needs its own set, since these boundaries would clip its tail.

### B4 — Git operation duration · *derived* (n=145)

Observed: p50 32 ms · p95 76 ms · p99 254 ms · max 274 ms.

```
0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30
```

Tail extends well past the observed maximum on purpose: the corpus contains no `git_push`
over SSH, whose duration is unbounded until #274 lands.

### B5 — Embedding HTTP duration · *derived* (n=169)

Observed: p50 386 ms · p95 831 ms · p99 2.58 s · max 5.6 s.

```
0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60
```

### B6 — Search phase duration · *derived* (n=58 per phase)

Observed: `fts` p50 149 ms / p99 2.04 s; `vec` p50 285 ms / p99 3.65 s.

```
0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15
```

One set, separated by the `phase` label.

### B7 — Contention wait · *designed for detection, no production signal*

`git_lock_wait_ms` p99 is 0.4 ms and `limiter_wait_ms` is identically zero across all 31
observations. These are distributions of an *uncontended* system: they say the problem has
not happened yet, not what it looks like when it does. Boundaries are chosen so that the
first non-trivial wait is visible and the tail is legible.

```
0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.05, 0.25, 1, 5, 10
```

Covers `git_lock_wait`, `limiter_wait` and the `run_sync` dispatch categories of §1.7.

### B8 — Queue age · *designed*

The observed `queue_wait_ms` range (n=42, 472–862 ms) is the worker poll interval showing
through on an idle queue, not a backlog distribution. Alerting is about sustained age, so
the set spans seconds to hours.

```
1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600, 7200, 21600
```

### B9 — Job execution duration · *designed* (n=27, too small to derive)

```
0.05, 0.25, 1, 2.5, 5, 10, 30, 60, 300, 900, 1800, 3600
```

---

## Part 3 — Families

Columns: every family names its owning role (per #280's deployment contract) and whether
it aggregates per-process or per-deployment.

§3.1 lists families whose observation point exists today; each row is implementable from
this table alone. §3.2 lists families whose observation point does not exist yet, naming
the issue that creates it and therefore enters the family.

### 3.1 Entered

| family | type | labels | buckets | observation point | role | aggregation |
| --- | --- | --- | --- | --- | --- | --- |
| `kajet_http_request_duration_seconds` | histogram | `route`, `method` | B1 | `LoggingMiddleware.send_wrapper` at `http.response.start` (`log.py:340`) | `api`, `mcp` | per-process → summed at exposition |
| `kajet_http_requests_total` | counter | `route`, `method`, `status_class` | — | same | `api`, `mcp` | per-process |
| `kajet_mcp_tool_duration_seconds` | histogram | `tool` | B2 | `ToolDispatchMiddleware.on_call_tool`, whole dispatch including validation and target resolution | `mcp` | per-process |
| `kajet_mcp_tool_calls_total` | counter | `tool`, `outcome` | — | same; `outcome` ∈ {`ok`, `error`, `invalid`} | `mcp` | per-process |
| `kajet_db_transaction_duration_seconds` | histogram | `operation` | B3 | `DbRepository.operation()` / `timed_session()` | all | per-process |
| `kajet_db_operations_total` | counter | `operation`, `outcome` | — | same; `outcome` ∈ {`ok`, `error`} | all | per-process |
| `kajet_git_operation_duration_seconds` | histogram | `operation` | B4 | the `git_ms` measurement seam | `api`, `mcp`, `worker` | per-process |
| `kajet_git_lock_wait_seconds` | histogram | — | B7 | the `git_lock_wait_ms` seam, in-process lock plus `flock` | `api`, `mcp`, `worker` | per-process |
| `kajet_runsync_wait_seconds` | histogram | `category` | B7 | `run_sync`'s limiter acquisition (`concurrency.py:57`) | `api`, `mcp` | per-process |
| `kajet_runsync_limiter_borrowed` | gauge `livesum` | — | — | `limiter.borrowed_tokens` | `api`, `mcp` | per-deployment |
| `kajet_embedding_http_duration_seconds` | histogram | `phase` | B5 | the `embed_http_ms` seam, per HTTP attempt including retries | `worker`, `api` | per-process |
| `kajet_embedding_cache_events_total` | counter | `result` | — | `embed_cache_hits` / `embed_cache_misses`; a hit performs no HTTP request | `worker`, `api` | per-process |
| `kajet_search_phase_duration_seconds` | histogram | `phase` | B6 | the `fts_ms` / `vec_ms` seams | `api`, `mcp` | per-process |
| `kajet_job_duration_seconds` | histogram | `kind` | B9 | `run_job` (`worker.py:42`), handler execution only | `worker`, `all` | per-deployment |
| `kajet_job_queue_wait_seconds` | histogram | `kind` | B8 | `run_job`'s `queue_wait_ms`, measured against `next_run_at` | `worker`, `all` | per-deployment |
| `kajet_job_transitions_total` | counter | `kind`, `outcome` | — | `complete` / `fail` / `fail_terminal`, counted after the repository write commits | `worker`, `all` | per-deployment |

`kind` domain (from the handler registry, `server.py:42`): `push_workspace`,
`reconcile_links`, `heal_dangling`, `sweep_outbox`, `embed_note`, `reindex_note`.
`heal_dangling` exists only to drain jobs written before its handler was replaced; it
leaves the domain once the queue is confirmed empty.

### 3.2 Deferred

| family | entered by | why it cannot be specified now |
| --- | --- | --- |
| SQLite / WAL / page-count / freelist gauges | #312 | No sampler exists; which quantities SQLite exposes on the deployed build is #310's capability matrix |
| Connection pool gauges | #312 | Same sampler |
| Queue depth by kind and state | #313 | Same sampler; the state domain follows the sampling query, not the model |
| Worker poll-loop health, sampler freshness | #313, #311 | Freshness semantics are defined with the sampler skeleton |
| Embedding logical-call family | #316 | The issue exists precisely because `_embed` has no timing seam yet |
| Search sub-phases beyond `fts` / `vec` | #315 | The sub-phases are created by that issue |
| File I/O and fsync attribution | #315 | No seam today; `workspace_write_ms` is the nearest existing field |

Each deferred family is entered into §3.1 by the pull request that creates its observation
point, satisfying the same Part 1 rules.

---

## Part 4 — Series budget

Computed from the real inventory: 44 REST route templates across 58 route+method
combinations, 42 registered MCP tools, ~50 qualified `repository.operation` names, 6 job
kinds, 5 `run_sync` categories. Bucket counts are the sets actually chosen in Part 2, not a
uniform assumption.

A classic histogram costs `finite_buckets + 1` (`+Inf`) `+ 2` (`_sum`, `_count`) series per
label combination — 15 for a 12-bucket set, 14 for an 11-bucket set.

| family group | combinations | series each | total |
| --- | --- | --- | --- |
| HTTP duration (B1) | 58 | 15 | 870 |
| MCP tool duration (B2) | 42 | 15 | 630 |
| DB transaction (B3) | 50 | 15 | 750 |
| Git operation (B4) | ~6 | 15 | 90 |
| Git lock wait (B7) | 1 | 15 | 15 |
| `run_sync` wait (B7) | 5 | 15 | 75 |
| Embedding HTTP (B5) | ~2 | 14 | 28 |
| Search phase (B6) | 2 | 14 | 28 |
| Job duration (B9) | 6 | 15 | 90 |
| Job queue wait (B8) | 6 | 15 | 90 |
| **Histograms** | | | **2 666** |
| HTTP requests (× ~4 status classes) | 232 | 1 | 232 |
| MCP tool calls (× 3 outcomes) | 126 | 1 | 126 |
| DB operations (× 2 outcomes) | 100 | 1 | 100 |
| Job transitions (× 3 outcomes) | 18 | 1 | 18 |
| Remaining counters — embedding cache, git, SQLite errors | ~30 | 1 | 30 |
| **Counters** | | | **506** |
| Live gauges — pool, limiter, in-flight | ~9 | 1 | 9 |
| Shared-state gauges — WAL, page/freelist, sampler freshness | ~14 | 1 | 14 |
| Queue depth — 6 kinds × 4 states | 24 | 1 | 24 |
| **Gauges** | | | **47** |
| **Total** | | | **≈3 219** |

**Ceiling 10 000 application series per scrape target. Headroom ≈3.1×.**

The same inventory with `outcome`/`status_class` also on histograms comes to ≈6 100
series — headroom ≈1.6×. That comparison is the entire justification for §1.4.

Nothing in this budget grows with the number of notes, workspaces or users. It grows only
when a route, tool, job kind or repository operation is added, at 15 series for a new
route or tool. #317 measures the real figure against this estimate.
