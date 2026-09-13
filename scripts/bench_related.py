"""Related-notes KNN benchmark (issue #209).

Measures the query shapes a per-note "related notes" read could take over the stored
vec0 chunk embeddings, on the production table shape (``note_chunks_vec_{dim}``,
``chunk_size=64``, ``(workspace, identity)`` partition keys — built through
``NoteChunkRepository.ensure_vec_table`` so it cannot drift from what the app creates).

Three phases, each on a fresh temp database with the real Alembic schema and the real
engine (QueuePool 5+5, WAL, sqlite-vec loaded per connection):

``quality``   A synthetic workspace whose vectors are drawn from a known topic model, so
              every (source, target) pair has a ground-truth relevance. Scores each
              evidence strategy x aggregation formula on nDCG, boilerplate false
              positives and minority-topic recall.
``latency``   Workspace sizes x source-note sizes, isolated end-to-end latency of each
              evidence strategy (one SQL statement + Python aggregation).
``concurrency`` C simultaneous note views through ``run_sync`` (the 10-slot limiter the
              app uses), with pool checkout tracking, plus a search stream alongside to
              see whether related-notes load starves search.

Vectors are synthetic on purpose: the benchmark must be repeatable and publishable, and
production embeddings are personal data. The generator mimics the geometry that matters
for ranking — an anisotropic common direction, topic clusters grouped into areas (graded
similarity), and near-duplicate boilerplate sections shared across many notes.

    uv run python scripts/bench_related.py --label local --out /tmp/related.json
    uv run python scripts/bench_related.py --phases latency --sizes 2138,20000
"""

import argparse
import asyncio
import json
import math
import os
import platform
import random
import statistics
import sys
import tempfile
import threading
import time
from array import array
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# Must precede the first SQLAlchemy import on a free-threaded build; kajet_turbo's own
# package __init__ does the same, but it is imported after sqlalchemy below.
os.environ.setdefault("DISABLE_SQLALCHEMY_CEXT_RUNTIME", "1")

from sqlalchemy import event, text
from sqlmodel import Session

from kajet_turbo.concurrency import run_sync
from kajet_turbo.db import Database
from kajet_turbo.embedding.identity import IndexIdentity
from kajet_turbo.repositories.notes.chunks import NoteChunkRepository

DIM = 3072
OWNER = "bench-owner"
WS = "bench"
IDENTITY = IndexIdentity(backend="https://bench.invalid/v1", model="synthetic", dim=DIM)
VEC_TABLE = f"note_chunks_vec_{DIM}"

# Production shape measured 2026-09-13 (read-only aggregate, anonymized): the largest
# workspace holds 2138 vectors, chunks per indexed note are p50 1 / p95 9 / p99 16 /
# max 26. PROD_CHUNK_BINS reproduces that histogram for the quality corpus.
PROD_LARGEST_WS = 2138
PROD_CHUNK_BINS = [(1, 727), (3, 424), (8, 134), (15, 41), (26, 1)]
SOURCE_BINS = (1, 5, 10, 25, 100)


# --- synthetic vector model ----------------------------------------------------


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(math.sumprod(v, v))
    return [x / n for x in v]


def _gauss(rng: random.Random) -> list[float]:
    # sigma 1/sqrt(DIM) makes the draw unit length to within ~1% without a
    # normalization pass — this loop dominates seeding time at 20k vectors.
    sigma = 1 / math.sqrt(DIM)
    return [rng.gauss(0.0, sigma) for _ in range(DIM)]


def _mix2(wa: float, a: list[float], wb: float, b: list[float]) -> list[float]:
    return _unit([wa * x + wb * y for x, y in zip(a, b, strict=True)])


def _mix3(
    wa: float, a: list[float], wb: float, b: list[float], wc: float, c: list[float]
) -> list[float]:
    return _unit([wa * x + wb * y + wc * z for x, y, z in zip(a, b, c, strict=True)])


# Similarity geometry of the production embeddings (text-embedding-3-large, 3072-d),
# measured 2026-09-13 on-host as aggregates only: cosine percentiles over random pairs
# and over each sampled chunk's 50 nearest chunks from other notes in its partition.
PROD_GEOMETRY = {
    "cross_note_pair_p50": 0.49,
    "cross_note_pair_p5_p95": (0.337, 0.635),
    "same_note_pair_p50": 0.735,
    "nearest_other_note_p50": 0.75,
    "share_chunks_with_ge5_neighbours_ge_0.8": 0.072,
    "share_chunks_with_ge5_neighbours_ge_0.85": 0.01,
    "top_hub_note_in_share_of_top10_lists": 0.163,
}


@dataclass
class VectorModel:
    """Topic-cluster vector model calibrated to PROD_GEOMETRY (``measure_geometry``
    reports the synthetic side next to it). Real embeddings of one person's notes are
    strongly anisotropic — an unrelated pair already sits at cosine ~0.49 — so the
    signal lives in a narrow 0.49 -> 0.75 band. The per-chunk weight of the common
    direction (``genericity``) varies: generic chunks sit close to everything, which is
    where both the pair-similarity spread and the hub notes seen in production come
    from. Random 3072-d Gaussians are near-orthogonal, so every direction below is
    effectively independent."""

    rng: random.Random
    n_areas: int = 25
    topics_per_area: int = 6
    genericity: tuple[float, float] = (0.40, 0.82)
    w_topic: float = 0.48
    # Per-topic noise: tight topics (recurring, templated notes) produce the dense
    # neighbourhoods production shows at cosine >= 0.8; loose ones the long tail.
    topic_noise: tuple[float, float] = (0.37, 0.58)
    w_boiler: float = 0.55
    w_boiler_noise: float = 0.35
    global_dir: list[float] = field(init=False)
    topics: list[list[float]] = field(init=False)
    noise: list[float] = field(init=False)
    boilerplate: list[float] = field(init=False)

    def __post_init__(self) -> None:
        self.global_dir = _unit(_gauss(self.rng))
        self.boilerplate = _unit(_gauss(self.rng))
        self.topics = []
        for _ in range(self.n_areas):
            area = _unit(_gauss(self.rng))
            for _ in range(self.topics_per_area):
                self.topics.append(_mix2(0.6, area, 0.8, _gauss(self.rng)))
        self.noise = [self.rng.uniform(*self.topic_noise) for _ in self.topics]

    @property
    def n_topics(self) -> int:
        return len(self.topics)

    def content_chunk(self, topic: int) -> bytes:
        vec = _mix3(
            self.rng.uniform(*self.genericity),
            self.global_dir,
            self.w_topic,
            self.topics[topic],
            self.noise[topic],
            _gauss(self.rng),
        )
        return array("f", vec).tobytes()

    def boilerplate_chunk(self) -> bytes:
        vec = _mix3(
            sum(self.genericity) / 2,
            self.global_dir,
            self.w_boiler,
            self.boilerplate,
            self.w_boiler_noise,
            _gauss(self.rng),
        )
        return array("f", vec).tobytes()


BOILER = -1  # chunk label for a template/boilerplate section
# Share of multi-chunk notes carrying a template section. 0.08 puts the synthetic
# neighbour density at >= 0.85 near the production share (see PROD_GEOMETRY).
BOILERPLATE_SHARE = 0.08


@dataclass
class SynthNote:
    note_id: str
    labels: list[int]  # one per chunk: topic index or BOILER

    def topic_mix(self) -> dict[int, float]:
        content = [t for t in self.labels if t != BOILER]
        if not content:
            return {}
        mix: dict[int, float] = defaultdict(float)
        for t in content:
            mix[t] += 1 / len(content)
        return mix

    @property
    def has_boilerplate(self) -> bool:
        return BOILER in self.labels


def relevance(src: SynthNote, tgt: SynthNote) -> float:
    """Histogram intersection of content-topic mixes: 1.0 = same topics in the same
    proportions, 0 = no shared topic. Boilerplate never contributes — two notes sharing
    only a template section are unrelated by definition."""
    a, b = src.topic_mix(), tgt.topic_mix()
    return sum(min(a[t], b[t]) for t in a.keys() & b.keys())


def _zipf_topic(rng: random.Random, n_topics: int) -> int:
    # Popular topics recur in dozens of notes, the tail in one or two — the shape a
    # personal notebook has. s=1.1 over 150 topics puts ~9% of notes on the top topic.
    weights = [1 / (r + 1) ** 1.1 for r in range(n_topics)]
    return rng.choices(range(n_topics), weights=weights)[0]


def compose_note(rng: random.Random, note_id: str, n_chunks: int, n_topics: int) -> SynthNote:
    """1-3 topics per note, each a contiguous section, the first dominant. Multi-chunk
    notes carry a template section with probability BOILERPLATE_SHARE."""
    k_topics = 1 if n_chunks == 1 else rng.choices([1, 2, 3], weights=[55, 30, 15])[0]
    k_topics = min(k_topics, n_chunks)
    topics: list[int] = []
    while len(topics) < k_topics:
        t = _zipf_topic(rng, n_topics)
        if t not in topics:
            topics.append(t)
    boiler = n_chunks >= 2 and rng.random() < BOILERPLATE_SHARE
    content_slots = n_chunks - (1 if boiler else 0)
    # Dominant-first split, every topic at least one chunk: [6,3,1]-style sections.
    split = [1] * k_topics
    for _ in range(content_slots - k_topics):
        split[min(int(rng.expovariate(1.2)), k_topics - 1)] += 1
    labels = [t for t, n in zip(topics, split, strict=True) for _ in range(n)]
    if boiler:
        labels.append(BOILER)
    return SynthNote(note_id=note_id, labels=labels)


# --- database seeding ------------------------------------------------------------


@dataclass
class Fixture:
    db: Database
    notes: dict[str, SynthNote]
    tmp: tempfile.TemporaryDirectory

    def close(self) -> None:
        self.db.close()
        self.tmp.cleanup()


def build_fixture(notes: list[SynthNote], model: VectorModel, mmap_size: int = 0) -> Fixture:
    """``mmap_size`` > 0 adds ``PRAGMA mmap_size`` to every pooled connection — NOT what
    production runs today (0 = SQLite default, reads go through pread into each
    connection's own page cache). Exposed to measure that difference, see #209."""
    tmp = tempfile.TemporaryDirectory(prefix="bench-related-")
    db = Database(str(Path(tmp.name) / "bench.db"))
    if mmap_size:

        @event.listens_for(db.engine, "connect")
        def _mmap(conn, _record) -> None:
            conn.execute(f"PRAGMA mmap_size={int(mmap_size)}")

        db.engine.dispose()  # connections opened by migrations predate the listener
    NoteChunkRepository(db.engine).ensure_vec_table(IDENTITY)
    now = "2026-09-13T00:00:00+00:00"
    rowid = 0
    with Session(db.engine) as session:
        conn = session.connection()
        for note in notes:
            conn.execute(
                text(
                    "INSERT INTO notes (id, workspace, owner_id, title, folder,"
                    " created_at, updated_at, index_generation, index_state, indexed_at)"
                    " VALUES (:id, :ws, :o, :t, '', :now, :now, 1, 'indexed', :now)"
                ),
                {"id": note.note_id, "ws": WS, "o": OWNER, "t": note.note_id, "now": now},
            )
            for ordinal, label in enumerate(note.labels):
                rowid += 1
                chunk_id = f"{note.note_id}-{ordinal}"
                conn.execute(
                    text(
                        "INSERT INTO note_chunks (chunk_rowid, id, note_id, workspace,"
                        " owner_id, ordinal, header_path, content, char_start, char_end,"
                        " dim, created_at)"
                        " VALUES (:r, :id, :n, :ws, :o, :ord, '[]', '', 0, 0, :dim, :now)"
                    ),
                    {
                        "r": rowid,
                        "id": chunk_id,
                        "n": note.note_id,
                        "ws": WS,
                        "o": OWNER,
                        "ord": ordinal,
                        "dim": DIM,
                        "now": now,
                    },
                )
                emb = model.boilerplate_chunk() if label == BOILER else model.content_chunk(label)
                conn.execute(
                    text(
                        f"INSERT INTO {VEC_TABLE}"
                        " (chunk_rowid, embedding, workspace, identity, owner_id, note_id,"
                        " chunk_id) VALUES (:r, :e, :ws, :i, :o, :n, :c)"
                    ),
                    {
                        "r": rowid,
                        "e": emb,
                        "ws": WS,
                        "i": IDENTITY.key,
                        "o": OWNER,
                        "n": note.note_id,
                        "c": chunk_id,
                    },
                )
        session.commit()
    return Fixture(db=db, notes={n.note_id: n for n in notes}, tmp=tmp)


# --- evidence strategies ------------------------------------------------------------
#
# Evidence = rows of (source_key, target_note_id, distance): for each source vector, its
# k nearest chunks from OTHER notes in the same (workspace, identity) partition, reduced
# to the best chunk per target note. `note_id != :nid` is a vec0 metadata filter applied
# inside the KNN, so a note's own chunks never consume k.

_SRC_ALL = (
    "SELECT v.chunk_rowid AS sid, v.embedding AS emb"
    " FROM note_chunks c JOIN {vec} v ON v.chunk_rowid = c.chunk_rowid"
    " WHERE c.note_id = :nid AND c.dim = :dim"
)
_SRC_IDS = (
    "SELECT v.chunk_rowid AS sid, v.embedding AS emb FROM {vec} v"
    " WHERE v.chunk_rowid IN (SELECT value FROM json_each(:ids))"
)
_KNN_JOIN = (
    "WITH src AS MATERIALIZED ({src})"
    " SELECT src.sid AS sid, t.note_id AS note_id, MIN(t.distance) AS distance"
    " FROM src JOIN {vec} t"
    "  ON t.embedding MATCH src.emb AND t.k = :k"
    "  AND t.workspace = :ws AND t.identity = :ident AND t.owner_id = :o"
    "  AND t.note_id != :nid"
    " GROUP BY src.sid, t.note_id"
)
_CENTROID_KNN = (
    "WITH hits AS MATERIALIZED ("
    "  SELECT note_id, distance FROM {vec}"
    "  WHERE embedding MATCH :emb AND k = :k"
    "  AND workspace = :ws AND identity = :ident AND owner_id = :o AND note_id != :nid"
    ")"
    " SELECT 0 AS sid, note_id, MIN(distance) AS distance FROM hits GROUP BY note_id"
)
_SOURCE_VECTORS = (
    "SELECT v.chunk_rowid, v.embedding FROM note_chunks c"
    f" JOIN {VEC_TABLE} v ON v.chunk_rowid = c.chunk_rowid"
    " WHERE c.note_id = :nid AND c.dim = :dim ORDER BY c.ordinal"
)

Evidence = list[tuple[int, str, float]]


def _params(nid: str, k: int) -> dict[str, object]:
    return {"nid": nid, "k": k, "ws": WS, "ident": IDENTITY.key, "o": OWNER, "dim": DIM}


def _knn_join(session: Session, nid: str, k: int, ids: list[int] | None) -> Evidence:
    src = (_SRC_ALL if ids is None else _SRC_IDS).format(vec=VEC_TABLE)
    params = _params(nid, k) | ({} if ids is None else {"ids": json.dumps(ids)})
    rows = session.connection().execute(text(_KNN_JOIN.format(src=src, vec=VEC_TABLE)), params)
    return [(r[0], r[1], r[2]) for r in rows]


def _source_vectors(session: Session, nid: str) -> list[tuple[int, array]]:
    rows = session.connection().execute(text(_SOURCE_VECTORS), {"nid": nid, "dim": DIM})
    out = []
    for rowid, blob in rows:
        vec = array("f")
        vec.frombytes(blob)
        out.append((rowid, vec))
    return out


def _dot(a: array, b: array) -> float:
    return math.sumprod(a, b)


def pick_spread(vectors: list[tuple[int, array]], cap: int) -> list[int]:
    """Evenly spaced by ordinal. Sections are contiguous runs of chunks, so a spread
    sample hits every sizeable section without looking at a single vector."""
    n = len(vectors)
    if n <= cap:
        return [rowid for rowid, _ in vectors]
    return [vectors[round(i * (n - 1) / (cap - 1))][0] for i in range(cap)]


def pick_farthest(vectors: list[tuple[int, array]], cap: int) -> list[int]:
    """Greedy farthest-first from the first chunk: each pick maximizes its minimum
    distance to the picks so far. Deterministic; O(cap * n) dot products."""
    n = len(vectors)
    if n <= cap:
        return [rowid for rowid, _ in vectors]
    chosen = [0]
    best_sim = [_dot(vectors[0][1], v) for _, v in vectors]
    while len(chosen) < cap:
        nxt = min((i for i in range(n) if i not in chosen), key=lambda i: best_sim[i])
        chosen.append(nxt)
        for i, (_, v) in enumerate(vectors):
            best_sim[i] = max(best_sim[i], _dot(vectors[nxt][1], v))
    return [vectors[i][0] for i in sorted(chosen)]


def centroid(vectors: list[tuple[int, array]]) -> bytes:
    acc = [0.0] * DIM
    for _, v in vectors:
        for i, x in enumerate(v):
            acc[i] += x
    return array("f", _unit(acc)).tobytes()


@dataclass(frozen=True)
class Strategy:
    name: str
    k: int
    cap: int | None = None  # source-chunk cap; None = every source chunk
    pick: str | None = None  # "spread" | "farthest" | "centroid"

    def evidence(self, session: Session, nid: str) -> tuple[Evidence, int]:
        """Returns (evidence, number of source vectors actually queried)."""
        if self.pick is None:
            ev = _knn_join(session, nid, self.k, None)
            return ev, len({sid for sid, _, _ in ev})
        vectors = _source_vectors(session, nid)
        if self.pick == "centroid":
            params = _params(nid, self.k) | {"emb": centroid(vectors)}
            rows = session.connection().execute(text(_CENTROID_KNN.format(vec=VEC_TABLE)), params)
            return [(r[0], r[1], r[2]) for r in rows], 1
        assert self.cap is not None
        picker = pick_spread if self.pick == "spread" else pick_farthest
        ids = picker(vectors, self.cap)
        return _knn_join(session, nid, self.k, ids), len(ids)


STRATEGIES = [
    Strategy("full-k10", k=10),
    Strategy("full-k25", k=25),
    Strategy("full-k50", k=50),
    Strategy("full-k100", k=100),
    Strategy("spread8-k25", k=25, cap=8, pick="spread"),
    Strategy("spread16-k25", k=25, cap=16, pick="spread"),
    Strategy("spread16-k50", k=50, cap=16, pick="spread"),
    Strategy("farthest8-k25", k=25, cap=8, pick="farthest"),
    Strategy("centroid-k100", k=100, pick="centroid"),
]


# --- aggregation formulas -----------------------------------------------------------
#
# All vectors are unit length, so cosine = 1 - d^2 / 2 for vec0's L2 distance.


def _cos(distance: float) -> float:
    return 1.0 - distance * distance / 2.0


def _per_source(evidence: Evidence) -> dict[int, dict[str, float]]:
    by_src: dict[int, dict[str, float]] = defaultdict(dict)
    for sid, nid, dist in evidence:
        by_src[sid][nid] = _cos(dist)
    return by_src


def agg_best(evidence: Evidence, n_sources: int) -> dict[str, float]:
    scores: dict[str, float] = {}
    for _, nid, dist in evidence:
        scores[nid] = max(scores.get(nid, -1.0), _cos(dist))
    return scores


def agg_best_cov(lam: float) -> Callable[[Evidence, int], dict[str, float]]:
    """Best match qualifies, coverage (share of source chunks whose top-k reached the
    target) boosts. The product contract for #189, with lam as the boost weight."""

    def agg(evidence: Evidence, n_sources: int) -> dict[str, float]:
        best = agg_best(evidence, n_sources)
        hits: dict[str, int] = defaultdict(int)
        for _, nid, _ in evidence:
            hits[nid] += 1
        return {nid: best[nid] + lam * hits[nid] / n_sources for nid in best}

    return agg


def agg_rrf(evidence: Evidence, n_sources: int, c: int = 60) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for targets in _per_source(evidence).values():
        ranked = sorted(targets, key=targets.__getitem__, reverse=True)
        for rank, nid in enumerate(ranked):
            scores[nid] += 1.0 / (c + rank + 1)
    return scores


def _hub_adjusted(evidence: Evidence) -> list[tuple[int, str, float]]:
    """Subtract each source chunk's mean similarity to its own neighbour list (the
    source half of CSLS, Conneau et al. 2018). A boilerplate chunk whose top-k is a wall
    of 0.93 near-duplicates nets ~0 against every one of them; a content chunk keeps
    the margin by which its best matches stand out. Needs no extra query — the
    neighbour list is the evidence already fetched."""
    out = []
    for sid, targets in _per_source(evidence).items():
        r = statistics.fmean(targets.values())
        out.extend((sid, nid, sim - r) for nid, sim in targets.items())
    return out


def agg_hub_best_cov(lam: float) -> Callable[[Evidence, int], dict[str, float]]:
    def agg(evidence: Evidence, n_sources: int) -> dict[str, float]:
        best: dict[str, float] = {}
        hits: dict[str, int] = defaultdict(int)
        for _, nid, adj in _hub_adjusted(evidence):
            best[nid] = max(best.get(nid, -9.0), adj)
            hits[nid] += 1
        return {nid: best[nid] + lam * hits[nid] / n_sources for nid in best}

    return agg


_TAIL = -10.0  # below any head score: hub margins and coverage live in [-2, 1.2]


def agg_hub_head(lam: float, head: int = 10) -> Callable[[Evidence, int], dict[str, float]]:
    """hub-best+cov computed from each source's ``head`` nearest distinct notes only —
    the hub mean r, the best margin and the coverage hit alike. Notes seen only past a
    source's head are fill: ranked after every head note, by their own margin.

    Invariant: once k is large enough that every source's list holds ``head`` distinct
    notes, a larger k cannot change any head (extra chunks are all farther than the
    ones already seen), so the ranked head is identical for every such k — k can
    follow the caller's limit (MCP allows 50, and a one-chunk note needs k >= limit to
    fill it) without moving what REST and MCP show first."""

    def agg(evidence: Evidence, n_sources: int) -> dict[str, float]:
        best: dict[str, float] = {}
        tail: dict[str, float] = {}
        hits: dict[str, int] = defaultdict(int)
        for targets in _per_source(evidence).values():
            ranked = sorted(targets, key=lambda nid: (-targets[nid], nid))
            r = statistics.fmean(targets[nid] for nid in ranked[:head])
            for nid in ranked[:head]:
                best[nid] = max(best.get(nid, _TAIL), targets[nid] - r)
                hits[nid] += 1
            for nid in ranked[head:]:
                tail[nid] = max(tail.get(nid, _TAIL), targets[nid] - r)
        scores = {nid: best[nid] + lam * hits[nid] / n_sources for nid in best}
        scores |= {nid: _TAIL + margin for nid, margin in tail.items() if nid not in best}
        return scores

    return agg


AGGREGATIONS: dict[str, Callable[[Evidence, int], dict[str, float]]] = {
    "hub10-best+cov0.05": agg_hub_head(0.05),
    "hub10-best+cov0.10": agg_hub_head(0.10),
    "hub10-best+cov0.15": agg_hub_head(0.15),
    "best": agg_best,
    "best+cov0.05": agg_best_cov(0.05),
    "best+cov0.15": agg_best_cov(0.15),
    "rrf": agg_rrf,
    "hub-best": agg_hub_best_cov(0.0),
    "hub-best+cov0.05": agg_hub_best_cov(0.05),
    "hub-best+cov0.15": agg_hub_best_cov(0.15),
}


def top_n(scores: dict[str, float], n: int) -> list[str]:
    # note_id as the tie-breaker keeps rankings deterministic across runs.
    return sorted(scores, key=lambda nid: (-scores[nid], nid))[:n]


# --- phase: quality -----------------------------------------------------------------


def _ndcg(ranked: list[str], rel: dict[str, float], n: int) -> float | None:
    ideal = sorted(rel.values(), reverse=True)[:n]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    if idcg == 0:
        return None
    dcg = sum(rel.get(nid, 0.0) / math.log2(i + 2) for i, nid in enumerate(ranked[:n]))
    return dcg / idcg


def prod_like_notes(rng: random.Random, n_topics: int, scale: float = 1.0) -> list[SynthNote]:
    notes = []
    for size, count in PROD_CHUNK_BINS:
        for _ in range(max(1, round(count * scale))):
            # Jitter within the bin so it is a distribution, not five spikes.
            n = max(1, size + rng.randint(-(size // 3), size // 3))
            notes.append(compose_note(rng, f"n{len(notes):05d}", n, n_topics))
    return notes


def _pct(xs: list[float]) -> dict[str, float]:
    s = sorted(xs)
    return {f"p{q}": round(s[min(len(s) - 1, int(q / 100 * (len(s) - 1)))], 3) for q in (5, 50, 95)}


def measure_geometry(fx: Fixture, rng: random.Random, n: int = 400) -> dict[str, object]:
    """The statistics PROD_GEOMETRY was measured with, computed on the synthetic
    fixture, so a reader can check the calibration instead of trusting it."""
    with Session(fx.db.engine) as session:
        conn = session.connection()
        rows = conn.execute(text(f"SELECT chunk_rowid, note_id FROM {VEC_TABLE}")).fetchall()
        blob_rows = conn.execute(text(f"SELECT chunk_rowid, embedding FROM {VEC_TABLE}"))
        blobs: dict[int, bytes] = {row[0]: row[1] for row in blob_rows}

        def vec(rowid: int) -> array:
            a = array("f")
            a.frombytes(blobs[rowid])
            return a

        by_note: dict[str, list[int]] = defaultdict(list)
        for rowid, nid in rows:
            by_note[nid].append(rowid)
        cross = []
        while len(cross) < 600:
            (a, na), (b, nb) = rng.sample(rows, 2)
            if na != nb:
                cross.append(math.sumprod(vec(a), vec(b)))
        multi = [ids for ids in by_note.values() if len(ids) >= 2]
        pairs = [rng.sample(rng.choice(multi), 2) for _ in range(600)]
        same = [math.sumprod(vec(x), vec(y)) for x, y in pairs]
        nearest, dense80, dense85 = [], 0, 0
        occurrence: dict[str, int] = defaultdict(int)
        sample = rng.sample(rows, min(n, len(rows)))
        for rowid, nid in sample:
            hits = conn.execute(
                text(
                    f"SELECT note_id, distance FROM {VEC_TABLE} WHERE embedding MATCH :e"
                    " AND k = 50 AND workspace = :ws AND identity = :i AND note_id != :n"
                ),
                {"e": blobs[rowid], "ws": WS, "i": IDENTITY.key, "n": nid},
            ).fetchall()
            sims = [_cos(d) for _, d in hits]
            nearest.append(sims[0])
            dense80 += sum(x >= 0.8 for x in sims) >= 5
            dense85 += sum(x >= 0.85 for x in sims) >= 5
            seen: list[str] = []
            for target, _ in hits:
                if target not in seen:
                    seen.append(target)
            for target in seen[:10]:
                occurrence[target] += 1
    return {
        "cross_note_pair": _pct(cross),
        "same_note_pair": _pct(same),
        "nearest_other_note": _pct(nearest),
        "share_chunks_with_ge5_neighbours_ge_0.8": round(dense80 / len(sample), 3),
        "share_chunks_with_ge5_neighbours_ge_0.85": round(dense85 / len(sample), 3),
        "top_hub_note_in_share_of_top10_lists": round(max(occurrence.values()) / len(sample), 3),
        "prod": PROD_GEOMETRY,
    }


def run_quality(seed: int, n_sources: int) -> dict:
    rng = random.Random(seed)
    model = VectorModel(rng)
    notes = prod_like_notes(rng, model.n_topics, scale=0.6)
    t0 = time.perf_counter()
    fx = build_fixture(notes, model)
    print(
        f"quality: {len(notes)} notes / {sum(len(n.labels) for n in notes)} chunks"
        f" seeded in {time.perf_counter() - t0:.1f}s",
        file=sys.stderr,
    )
    try:
        # Sources: every multi-chunk note (where strategies can differ) up to the budget,
        # then single-chunk notes to fill it.
        multi = [n for n in notes if len(n.labels) > 1]
        single = [n for n in notes if len(n.labels) == 1]
        rng.shuffle(multi)
        rng.shuffle(single)
        sources = (multi + single)[:n_sources]
        results: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        with Session(fx.db.engine) as session:
            for src in sources:
                rel = {
                    nid: r
                    for nid, tgt in fx.notes.items()
                    if nid != src.note_id and (r := relevance(src, tgt)) > 0
                }
                if not rel:
                    continue
                src_topics = src.topic_mix()
                minor = min(src_topics, key=src_topics.__getitem__) if len(src_topics) > 1 else None
                for strat in STRATEGIES:
                    ev, n_src = strat.evidence(session, src.note_id)
                    for agg_name, agg in AGGREGATIONS.items():
                        if strat.pick == "centroid" and agg_name != "best":
                            continue  # one source vector: every formula collapses to best
                        ranked = top_n(agg(ev, max(n_src, 1)), 10)
                        key = f"{strat.name}|{agg_name}"
                        m = results[key]
                        m["ndcg@5"].append(_ndcg(ranked, rel, 5) or 0.0)
                        m["ndcg@10"].append(_ndcg(ranked, rel, 10) or 0.0)
                        misses = [nid for nid in ranked[:5] if nid not in rel]
                        m["irrelevant@5"].append(len(misses) / 5)
                        # The production hub effect: long notes win best-chunk-match by
                        # having more chunks to draw a spurious near-match from.
                        m["long_irrelevant@5"].append(
                            sum(len(fx.notes[nid].labels) >= 10 for nid in misses) / 5
                        )
                        if src.has_boilerplate:
                            junk = [
                                nid
                                for nid in ranked[:5]
                                if nid not in rel and fx.notes[nid].has_boilerplate
                            ]
                            m["boiler_fp@5"].append(len(junk) / 5)
                        if minor is not None and any(
                            minor in fx.notes[nid].topic_mix() for nid in rel
                        ):
                            m["minor_recall@10"].append(
                                float(any(minor in fx.notes[nid].topic_mix() for nid in ranked))
                            )
        summary = {
            key: {metric: round(statistics.fmean(vals), 4) for metric, vals in m.items()}
            | {
                "n_boiler": len(m.get("boiler_fp@5", [])),
                "n_minor": len(m.get("minor_recall@10", [])),
            }
            for key, m in results.items()
        }
        geometry = measure_geometry(fx, random.Random(seed + 1))
        return {
            "notes": len(notes),
            "sources": len(sources),
            "geometry": geometry,
            "results": summary,
        }
    finally:
        fx.close()


# --- phase: latency -----------------------------------------------------------------


def latency_notes(
    rng: random.Random, n_topics: int, ws_size: int, per_bin: int
) -> tuple[list[SynthNote], dict[int, list[str]]]:
    """``per_bin`` planted source notes of exactly each SOURCE_BINS size, the rest of
    the partition filled with prod-distributed notes up to ``ws_size`` vectors."""
    planted: dict[int, list[str]] = defaultdict(list)
    notes: list[SynthNote] = []
    for size in SOURCE_BINS:
        for _ in range(per_bin):
            nid = f"s{size:03d}-{len(planted[size])}"
            notes.append(compose_note(rng, nid, size, n_topics))
            planted[size].append(nid)
    filled = sum(len(n.labels) for n in notes)
    sizes = [s for s, c in PROD_CHUNK_BINS for _ in range(c)]
    while filled < ws_size:
        n = min(rng.choice(sizes), ws_size - filled)
        notes.append(compose_note(rng, f"n{len(notes):06d}", n, n_topics))
        filled += n
    return notes, planted


def _timed_related(engine, strat: Strategy, nid: str, agg) -> float:
    t0 = time.perf_counter()
    with Session(engine) as session:
        ev, n_src = strat.evidence(session, nid)
        top_n(agg(ev, max(n_src, 1)), 10)
    return (time.perf_counter() - t0) * 1000


def _single_knn_ms(engine, emb: bytes, k: int = 50) -> float:
    """The production search leg's exact SQL, for the local→prod scale factor."""
    sql = NoteChunkRepository._vec_search_sql(DIM)
    t0 = time.perf_counter()
    with Session(engine) as session:
        session.connection().execute(
            text(sql), {"emb": emb, "k": k, "ws": WS, "ident": IDENTITY.key, "o": OWNER}
        ).fetchall()
    return (time.perf_counter() - t0) * 1000


def pct(xs: list[float]) -> dict[str, float]:
    s = sorted(xs)

    def at(p: float) -> float:
        return round(s[min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))], 2)

    return {"p50": at(50), "p95": at(95), "max": round(s[-1], 2), "n": len(s)}


def run_latency(
    seed: int, ws_sizes: list[int], per_bin: int, reps: int, mmap_size: int = 0
) -> dict:
    agg = agg_hub_best_cov(0.05)
    out: dict[str, dict] = {}
    for ws_size in ws_sizes:
        rng = random.Random(seed + ws_size)
        model = VectorModel(rng)
        notes, planted = latency_notes(rng, model.n_topics, ws_size, per_bin)
        t0 = time.perf_counter()
        fx = build_fixture(notes, model, mmap_size)
        n_vec = sum(len(n.labels) for n in notes)
        print(
            f"latency: ws={n_vec} vectors seeded in {time.perf_counter() - t0:.1f}s",
            file=sys.stderr,
        )
        try:
            engine = fx.db.engine
            # Warm the partition once: a cold first read is the page-cache cost every
            # process pays after a restart, measured separately below.
            probe = VectorModel(random.Random(seed)).content_chunk(0)
            cold = _single_knn_ms(engine, probe)
            knn = [_single_knn_ms(engine, probe) for _ in range(20)]
            row: dict[str, object] = {
                "vectors": n_vec,
                "single_knn_k50_cold_ms": round(cold, 2),
                "single_knn_k50": pct(knn),
            }
            for strat in STRATEGIES:
                for size in SOURCE_BINS:
                    times = [
                        _timed_related(engine, strat, nid, agg)
                        for nid in planted[size]
                        for _ in range(reps)
                    ]
                    row[f"{strat.name}|src{size}"] = pct(times)
            out[str(n_vec)] = row
        finally:
            fx.close()
    return out


# --- phase: concurrency -------------------------------------------------------------


@dataclass
class PoolTracker:
    checked_out: int = 0
    peak: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def attach(self, engine) -> None:
        @event.listens_for(engine, "checkout")
        def _out(*_):
            with self.lock:
                self.checked_out += 1
                self.peak = max(self.peak, self.checked_out)

        @event.listens_for(engine, "checkin")
        def _in(*_):
            with self.lock:
                self.checked_out -= 1


async def _load(
    engine, strat: Strategy, agg, nids: list[str], concurrency: int, rounds: int, search_emb: bytes
) -> dict:
    related: list[float] = []
    search: list[float] = []

    async def viewer(i: int) -> None:
        for r in range(rounds):
            nid = nids[(i + r) % len(nids)]
            t0 = time.perf_counter()
            await run_sync(_timed_related, engine, strat, nid, agg)
            related.append((time.perf_counter() - t0) * 1000)

    async def searcher() -> None:
        # One search stream alongside: does related-notes load starve search?
        for _ in range(rounds * 2):
            t0 = time.perf_counter()
            await run_sync(_single_knn_ms, engine, search_emb)
            search.append((time.perf_counter() - t0) * 1000)

    t0 = time.perf_counter()
    await asyncio.gather(*(viewer(i) for i in range(concurrency)), searcher())
    wall = time.perf_counter() - t0
    return {
        "related": pct(related),
        "search_alongside": pct(search),
        "throughput_views_per_s": round(len(related) / wall, 1),
    }


def run_concurrency(
    seed: int,
    ws_size: int,
    source_size: int,
    levels: list[int],
    rounds: int,
    mmap_size: int = 0,
    strategy: str = "full-k25",
    aggregation: str = "hub-best+cov0.05",
) -> dict:
    rng = random.Random(seed + 7)
    model = VectorModel(rng)
    notes, planted = latency_notes(rng, model.n_topics, ws_size, per_bin=10)
    fx = build_fixture(notes, model, mmap_size)
    try:
        engine = fx.db.engine
        tracker = PoolTracker()
        tracker.attach(engine)
        strat = next(st for st in STRATEGIES if st.name == strategy)
        agg = AGGREGATIONS[aggregation]
        search_emb = model.content_chunk(1)
        _single_knn_ms(engine, search_emb)  # warm
        out: dict[str, object] = {
            "vectors": sum(len(n.labels) for n in notes),
            "source_chunks": source_size,
            "strategy": strat.name,
        }
        for c in levels:
            tracker.peak = 0
            res = asyncio.run(
                _load(engine, strat, agg, planted[source_size], c, rounds, search_emb)
            )
            res["pool_peak_checked_out"] = tracker.peak
            out[f"c{c}"] = res
            print(f"concurrency c={c}: {res}", file=sys.stderr)
        return out
    finally:
        fx.close()


# --- report -------------------------------------------------------------------------


def print_report(result: dict) -> None:
    if "quality" in result:
        q = result["quality"]
        print(f"\n## Quality ({q['sources']} sources, {q['notes']} notes)\n")
        print(f"geometry (synthetic vs prod): {json.dumps(q['geometry'])}\n")
        print(
            "| strategy | aggregation | nDCG@5 | nDCG@10 | irrelevant@5 | long irrelevant@5"
            " | boiler FP@5 | minor recall@10 |"
        )
        print("|---|---|---|---|---|---|---|---|")
        for key, m in sorted(q["results"].items(), key=lambda kv: -kv[1]["ndcg@5"]):
            s, a = key.split("|")
            print(
                f"| {s} | {a} | {m['ndcg@5']:.3f} | {m['ndcg@10']:.3f}"
                f" | {m['irrelevant@5']:.3f} | {m['long_irrelevant@5']:.3f}"
                f" | {m.get('boiler_fp@5', 0):.3f} | {m.get('minor_recall@10', 0):.3f} |"
            )
    if "latency" in result:
        print("\n## Latency (ms, p50 / p95 per source-note size)\n")
        for ws, row in result["latency"].items():
            knn = row["single_knn_k50"]
            print(
                f"\n### {ws} vectors — single KNN k=50: p50 {knn['p50']} / p95 {knn['p95']}"
                f" (cold first read {row['single_knn_k50_cold_ms']})\n"
            )
            print("| strategy | " + " | ".join(f"src={s}" for s in SOURCE_BINS) + " |")
            print("|---|" + "---|" * len(SOURCE_BINS))
            for strat in STRATEGIES:
                cells = [row[f"{strat.name}|src{s}"] for s in SOURCE_BINS]
                print(
                    f"| {strat.name} | "
                    + " | ".join(f"{c['p50']} / {c['p95']}" for c in cells)
                    + " |"
                )
    if "concurrency" in result:
        c = result["concurrency"]
        print(
            f"\n## Concurrency ({c['vectors']} vectors, {c['source_chunks']}-chunk notes,"
            f" {c['strategy']})\n"
        )
        print("| viewers | related p50 / p95 | search alongside p50 / p95 | views/s | pool peak |")
        print("|---|---|---|---|---|")
        for key, res in c.items():
            if not key.startswith("c"):
                continue
            r, s = res["related"], res["search_alongside"]
            print(
                f"| {key[1:]} | {r['p50']} / {r['p95']} | {s['p50']} / {s['p95']}"
                f" | {res['throughput_views_per_s']} | {res['pool_peak_checked_out']} |"
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--label", default="local")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--seed", type=int, default=209)
    parser.add_argument("--phases", default="quality,latency,concurrency")
    parser.add_argument("--sources", type=int, default=250, help="quality: source notes scored")
    parser.add_argument(
        "--sizes",
        default=f"500,{PROD_LARGEST_WS},5000,20000",
        help="latency: workspace vector counts",
    )
    parser.add_argument(
        "--per-bin", type=int, default=3, help="latency: planted notes per source size"
    )
    parser.add_argument("--reps", type=int, default=5, help="latency: repetitions per planted note")
    parser.add_argument("--conc-size", type=int, default=PROD_LARGEST_WS)
    parser.add_argument("--conc-source", type=int, default=25, choices=SOURCE_BINS)
    parser.add_argument("--conc-levels", default="1,5,10,20")
    parser.add_argument("--conc-rounds", type=int, default=10)
    parser.add_argument(
        "--conc-strategy", default="full-k25", choices=[st.name for st in STRATEGIES]
    )
    parser.add_argument("--conc-agg", default="hub-best+cov0.05", choices=list(AGGREGATIONS))
    parser.add_argument(
        "--mmap-size",
        type=int,
        default=0,
        help="PRAGMA mmap_size for latency/concurrency connections (0 = production today)",
    )
    args = parser.parse_args()

    phases = set(args.phases.split(","))
    result: dict[str, object] = {
        "label": args.label,
        "seed": args.seed,
        "python": sys.version,
        "gil_enabled": sys._is_gil_enabled(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpus": os.cpu_count(),
        "mmap_size": args.mmap_size,
    }
    if "quality" in phases:
        result["quality"] = run_quality(args.seed, args.sources)
    if "latency" in phases:
        sizes = [int(s) for s in args.sizes.split(",")]
        result["latency"] = run_latency(args.seed, sizes, args.per_bin, args.reps, args.mmap_size)
    if "concurrency" in phases:
        levels = [int(c) for c in args.conc_levels.split(",")]
        result["concurrency"] = run_concurrency(
            args.seed,
            args.conc_size,
            args.conc_source,
            levels,
            args.conc_rounds,
            args.mmap_size,
            args.conc_strategy,
            args.conc_agg,
        )
    print_report(result)
    if args.out:
        args.out.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\nwrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
