# Related-notes KNN: aggregation and latency budget (#209)

2026-09-13. Harness: `scripts/bench_related.py`. Raw results: `2026-09-13-related-*.json` in this
directory. Everything below is reproducible with the commands at the end.

## TL;DR

- **SQL shape:** one statement, a correlated vec0 self-join. The source vectors are read by rowid, and each
  one drives a KNN over the same `(workspace, identity)` partition with `note_id != :nid` applied inside
  vec0.
- **Source chunks:** at most **16**, picked evenly by ordinal ("spread"). Per-source **k = 50**.
- **Ranking:** `hub10-best + 0.10 · coverage`. Similarities are hub-corrected per source over that source's
  10 nearest distinct notes; one strong match qualifies a note, and coverage across source chunks boosts
  it. Notes seen only past a source's head fill the list after all head notes.
- **Supported range:** p95 ≤ 2 s isolated for `(workspace, identity)` partitions up to **~15,000 vectors**
  on production hardware today. The largest partition holds 2,138, and the worst existing note measures
  ~0.2 s.
- **Concurrency:** the pool is never exhausted (`run_sync`'s 10-slot limiter bounds checkouts at 10), but
  from ~5 concurrent views the limiter saturates and *every* DB call queues behind related-notes reads.
  #210 should give related-notes its own small per-process semaphore (2). `PRAGMA mmap_size` (#372) raises
  vec0 throughput 1.6x on production hardware and makes it scale with threads.

## Production shape (read-only aggregate, 2026-09-13)

- **Partitions:** 10 `(workspace, identity)` partitions, one identity (3072-d). Vector counts: 2138, 713,
  210, 203, 193, 99, 56, 47, 5, 4.
- **Notes:** 1,327 indexed notes.
- **Chunks per note:** p50 1, p75 3, p90 7, p95 9, p99 16, max 26. Bins: 1 → 727, 2–5 → 424, 6–10 → 134,
  11–25 → 41, 26–50 → 1.
- **Consequence:** no note exceeds 26 chunks, so the issue's 100-chunk bin is hypothetical today.

## Is the synthetic geometry realistic?

Production embeddings of one person's notes are strongly anisotropic. An unrelated pair already sits at
cosine ~0.49, so the useful signal lives in a narrow 0.49 → 0.75 band. The generator is calibrated to
statistics measured on-host (aggregates only), and the quality phase re-measures the same statistics on
the synthetic corpus:

| statistic | production | synthetic |
|---|---|---|
| cross-note random pair, p5 / p50 / p95 | 0.337 / 0.490 / 0.635 | 0.340 / 0.463 / 0.644 |
| same-note pair, p50 | 0.735 | 0.660 |
| nearest chunk from another note, p50 | 0.750 | 0.740 |
| chunks with ≥ 5 neighbours at cos ≥ 0.80 | 7.2 % | 7.5 % |
| chunks with ≥ 5 neighbours at cos ≥ 0.85 | 1.0 % | 0.5 % |
| most frequent note's share of top-10 lists (hubness) | 16.3 % | 12.8 % |

Where the calibration falls short:
- **Same-note pairs sit lower:** the synthetic corpus has more multi-topic notes than production.
- **Production hubs are stronger:** in production they are long notes. The top-10 hub notes have 6–26
  chunks against a partition mean of 2.7, because the max over many chunks buys more lottery tickets.
- **Uncalibrated generator:** the first run used a model with near-duplicate boilerplate at cos 0.93 and
  an unrelated baseline of 0.15. It ranked the formulas in the same order.

## Quality (synthetic, known ground truth)

- **Corpus:** 796 notes with the production chunk histogram, 150 topics in 25 areas, 1–3 topic sections
  per note, and 8 % of multi-chunk notes carrying a template section.
- **Relevance:** histogram intersection of content-topic mixes. Boilerplate never counts.
- **Sources:** 250.

| evidence | aggregation | nDCG@5 | nDCG@10 | irrelevant@5 | long irrelevant@5 | boilerplate FP@5 | minority-topic recall@10 |
|---|---|---|---|---|---|---|---|
| full-k50 | best | 0.751 | 0.770 | 0.139 | 0.030 | 0.906 | 0.620 |
| full-k50 | best+cov0.05 | 0.797 | 0.800 | 0.115 | 0.027 | 0.553 | 0.630 |
| full-k50 | rrf | 0.808 | 0.809 | 0.129 | 0.018 | 0.059 | 0.560 |
| centroid-k100 | best | 0.832 | 0.840 | 0.086 | 0.013 | 0.094 | 0.470 |
| full-k25 | hub-best (mean over all k) | 0.713 | 0.733 | 0.173 | 0.024 | 0.471 | 0.970 |
| full-k50 | hub-best+cov0.05 (mean over all k) | 0.741 | 0.749 | 0.163 | 0.027 | 0.494 | 0.800 |
| spread16-k50 | hub10-best+cov0.05 | 0.783 | 0.801 | 0.116 | 0.012 | 0.024 | 0.910 |
| **spread16-k50** | **hub10-best+cov0.10** | **0.793** | **0.806** | **0.112** | **0.012** | **0.024** | **0.880** |
| spread16-k50 | hub10-best+cov0.15 | 0.801 | 0.808 | 0.112 | 0.011 | 0.024 | 0.860 |
| full-k50 | hub10-best+cov0.10 | 0.793 | 0.806 | 0.112 | 0.012 | 0.024 | 0.880 |
| full-k100 | hub10-best+cov0.10 | 0.793 | 0.806 | 0.112 | 0.012 | 0.024 | 0.880 |
| spread8-k25 | hub10-best+cov0.10 | 0.795 | 0.807 | 0.112 | 0.012 | 0.024 | 0.890 |
| farthest8-k25 | hub10-best+cov0.10 | 0.796 | 0.807 | 0.110 | 0.012 | 0.024 | 0.910 |

Metric definitions:
- *boilerplate FP@5:* irrelevant notes that share only a template section, over 17 sources that have one.
  The first run, with 50 such sources, gave the same ordering.
- *minority-topic recall@10:* for multi-topic sources, whether the top-10 contains any note covering the
  source's smallest section (100 sources). This is the contract's "one strong chunk match qualifies",
  measured directly.

Reading the table:
- **Best-match alone** hands the top-5 to template sections.
- **RRF and centroid** top nDCG@5 but fail the contract metric. Centroid loses subsection discovery
  (0.47), RRF lets dominant sections drown minority ones (0.56).
- **A hub correction whose mean runs over all k** degrades as k grows, because a wider k lowers each
  source's mean and lets hubs back in.
- **The `hub10` variant** takes the mean, the best margin and the coverage hit all from each source's 10
  nearest distinct notes. It is stable across k and across source strategies.
- **k-independence, verified:** over 300 sources, k=50 and k=100 produce identical ranked heads. Every
  k=25 vs k=50 difference traces to a k=25 list that held fewer than 10 distinct notes. So k=50 is the
  point where heads are full, and it also covers MCP's `limit` cap of 50 for single-chunk sources.
- **λ is a dial within sampling noise** (±0.03 at n=100): 0.05 favours minority recall, 0.15 favours
  nDCG. 0.10 is the midpoint; #214's calibration data is where to revisit it.
- **Bounded source sets cost nothing in quality here:** spread-8 and spread-16 match full aggregation.
  Farthest-first picks are no better than spread and cost Python dot products (production: 167 ms vs
  101 ms at 100 chunks, cap 8).

## Latency (isolated, p50 / p95 ms)

**Production host:** one-off throwaway container from the production image. AMD EPYC-Rome, 4 vCPU with
AVX2, container limited to 3 CPUs, `--network none`, temp DB. `mmap_size=0` is today's configuration.

| partition | strategy | src=1 | src=5 | src=10 | src=25 | src=100 |
|---|---|---|---|---|---|---|
| 2,144 | full-k25 | 13 / 13 | 62 / 64 | 123 / 125 | 302 / 323 | 1200 / 1234 |
| 2,144 | spread16-k25 | 15 / 16 | 64 / 68 | 123 / 129 | 198 / 216 | 200 / 218 |
| 2,144 | spread8-k25 | 13 / 16 | 63 / 67 | 98 / 107 | 101 / 106 | 101 / 107 |
| 2,144 | centroid-k100 | 15 / 15 | 16 / 16 | 20 / 21 | 21 / 23 | 36 / 38 |
| 5,022 | full-k25 | 31 / 35 | 150 / 166 | 309 / 344 | 754 / 790 | 2970 / 3053 |
| 5,022 | spread16-k25 | 32 / 34 | 150 / 156 | 299 / 307 | 481 / 506 | 483 / 493 |
| 5,022 | spread8-k25 | 32 / 34 | 151 / 163 | 243 / 253 | 244 / 251 | 242 / 246 |
| 5,022 | centroid-k100 | 33 / 35 | 34 / 38 | 36 / 37 | 40 / 50 | 54 / 58 |

Same host, `mmap_size=512 MB`:

| partition | strategy | src=1 | src=5 | src=10 | src=25 | src=100 |
|---|---|---|---|---|---|---|
| 2,144 | full-k25 | 9 / 10 | 45 / 47 | 85 / 91 | 215 / 216 | 831 / 847 |
| 2,144 | spread16-k25 | 10 / 10 | 43 / 45 | 85 / 85 | 135 / 138 | 132 / 138 |
| 5,022 | full-k25 | 21 / 21 | 100 / 105 | 197 / 199 | 492 / 510 | 1963 / 2022 |
| 5,022 | spread16-k25 | 23 / 24 | 101 / 104 | 209 / 214 | 322 / 329 | 319 / 330 |

- **The synthetic numbers transfer:** a single search-leg KNN (k=50) on the production host measures
  19.6 ms p50 at 2,144 synthetic vectors. The real `search_notes` median `vec_ms` in the 2,138-vector
  production workspace is ~28 ms (Loki, post-#264).
- **Cost model:** the self-join costs ≈ n_source × partition_vectors × 5.9 µs today (≈ 3.9 µs with mmap).
  Per-source k barely matters (k=10/25/50 within noise) because the partition scan dominates.
- **Linearity:** checked locally up to 20,088 vectors. spread16 went 153 → 635 ms p50 for 4.0x the
  vectors. The production run stopped at 5,022 to keep its CPU use short, so the 20k column is local-only
  and not reported here as production data.
- **Supported range (extrapolated linearly from the 5,022-vector production point):** with the 16-chunk
  cap, p95 reaches 2 s at ~20k vectors today and ~30k with mmap. The documented supported range is
  **≤ 15,000 vectors per partition**, which leaves headroom for per-request overhead. That is 7x today's
  largest partition. Uncapped full aggregation of the longest existing note (26 chunks) would reach the
  budget at ~12k.
- **Fallback if a partition outgrows the range:** drop the cap to 8. spread-8 measured equal quality here,
  so this doubles the range. File the performance issue then; no background cache.

## Concurrency (pool and starvation)

**Production host**, today's config, `full-k25` on 25-chunk notes (the most expensive shape an existing
note can produce), 2,141 vectors, plus one search stream alongside:

| concurrent views | related p50 / p95 | search alongside p50 / p95 | views/s | pool peak |
|---|---|---|---|---|
| 1 | 349 / 384 | 22 / 25 | 2.8 | 2 |
| 5 | 581 / 759 | 46 / 94 | 8.1 | 6 |
| 10 | 1285 / 1587 | 157 / 701 | 7.8 | 10 |
| 20 | 2369 / 2747 | 1355 / 1924 | 8.3 | 10 |

Same with `mmap_size=512 MB`: views/s 4.7 / 12.6 / 12.9 / 13.5, related p95 237 / 523 / 927 / 1665,
search-alongside p95 20 / 57 / 425 / 987.

**Selected design** (`spread16-k50` with the `hub10` aggregation; the run used λ = 0.05, which costs the
same as 0.10), local M-series:

| concurrent views | today: related p95 | today: search p95 | mmap: related p95 | mmap: search p95 |
|---|---|---|---|---|
| 1 | 109 | 17 | 28 | 5 |
| 5 | 562 | 70 | 56 | 7 |
| 10 | 1274 | 1073 | 161 | 57 |
| 20 | 1889 | 1670 | 297 | 200 |

- **Pool:** peak checkouts never exceeded 10 and no checkout timed out. `run_sync`'s limiter equals the
  pool size (5 + 5), so the pool cannot be exhausted from this path.
- **Saturation is the real risk:** once related-notes reads hold all 10 limiter slots, every other DB call
  in the process queues behind them. Search-alongside p95 went 25 → 701 ms at 10 views on the production
  host.
- **Without mmap, vec0 barely scales with threads:** `pread` copies each 768 KiB vec0 block into a
  per-connection page, and system time dominates (measured in #372). Throughput plateaus at ~8 views/s on
  production hardware.
- **Recommendation for #210:** a per-process `asyncio.Semaphore(2)` around the related-notes read, in
  front of `run_sync`. That leaves ≥ 8 slots for everything else and costs at most one queued view's
  latency in a burst.

## Selected design, for #210

```sql
-- :source_rowids = up to 16 chunk_rowids, spread evenly by ordinal over the note's chunks
-- (note_chunks WHERE note_id = :nid AND dim = :dim ORDER BY ordinal)
WITH src AS MATERIALIZED (
  SELECT v.chunk_rowid AS sid, v.embedding AS emb
  FROM note_chunks_vec_{dim} v
  WHERE v.chunk_rowid IN (SELECT value FROM json_each(:source_rowids))
)
-- optional folder scope, applied inside the KNN as vec0's one allowed rowid IN:
-- , scope AS MATERIALIZED (SELECT chunk_rowid FROM note_chunks WHERE note_id IN (...) AND dim = :dim)
SELECT src.sid AS source_rowid, t.note_id, t.chunk_id, MIN(t.distance) AS distance
FROM src JOIN note_chunks_vec_{dim} t
  ON t.embedding MATCH src.emb AND t.k = 50
 AND t.workspace = :ws AND t.identity = :ident AND t.owner_id = :owner
 AND t.note_id != :nid
 -- AND t.chunk_rowid IN (SELECT chunk_rowid FROM scope)
GROUP BY src.sid, t.note_id
```

Notes on the statement:
- **`t.chunk_id` rides along as a bare column:** SQLite documents that with a single `MIN()` aggregate,
  bare columns come from the row that holds the minimum. So each (source, target) row names the best
  matching target chunk, which is the heading/fragment the UI shows.
- **`note_id != :nid` is a vec0 metadata filter** evaluated inside the KNN, so a note's own chunks never
  consume k.
- **Folder scope, verified:** as a `rowid IN` pre-filter every source still gets its full k, and cost is
  unchanged.
- **Source vectors come from the current identity only.** A note whose chunks carry no vector under it is
  `pending`, not `empty`.
- **Owner check:** as in search, the authoritative owner check is the join to `notes` for the final top-N
  (title, folder, links).

Ranking, with vectors at unit length so cos = 1 − d²/2:
- For each source s, H_s is its 10 nearest distinct notes, and r_s is the mean cosine over H_s.
- `score(t) = max_{s : t ∈ H_s} (cos(s,t) − r_s) + 0.10 · |{s : t ∈ H_s}| / n_sources`.
- Notes that appear in no H_s rank after every head note, by their best `cos − r_s` (fill only).
- At most 16 × 50 = 800 evidence rows per call.

Per-result calibration metrics to return (for #214):
- raw best cosine;
- hub margin (`cos − r_s`);
- coverage (hits / n_sources);
- best source chunk id and best target chunk id.

Per response: `source_chunks_total`, `source_chunks_used` (whether the cap applied), `k`.

## Caveats

- **Synthetic ranking:** ranking quality is measured on synthetic vectors, so formula *ordering* is the
  claim, not absolute nDCG. Production data is personal and cannot be labelled here.
- **Stronger hubs in production:** production hubs are stronger than synthetic ones (16 % vs 13 %) and
  length-driven. `long irrelevant@5` stays ≤ 0.012 for the selected formula, but the first real
  calibration pass (#214) should check it.
- **Coverage of production measurements:** production latency was measured at ≤ 5,022 vectors, and
  production concurrency with `full-k25` on 25-chunk notes. Concurrency for the selected design was
  measured locally only.
- **mmap:** `mmap_size` is not configured in production. The mmap columns are what #372 would buy.

## Reproduce

```bash
uv run python scripts/bench_related.py --phases quality --out quality.json
uv run python scripts/bench_related.py --phases latency --sizes 500,2138,5000,20000
uv run python scripts/bench_related.py --phases concurrency --conc-strategy spread16-k50 --conc-agg hub10-best+cov0.10
uv run python scripts/bench_related.py --phases latency,concurrency --mmap-size 536870912
```
