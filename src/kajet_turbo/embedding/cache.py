"""Content-addressed embedding cache (DB-backed) + an in-memory LRU for query
embeddings.

The cache key is ``sha256(embedded_text) + backend + model``; vectors are stored as
little-endian float32 blobs (the format sqlite-vec consumes). Content-addressed ⇒
immutable for a given text+model, so there is no invalidation — only eviction, which is
out of scope here. The key has no ``owner_id``: identical chunk text is embedded once
and reused across users on the same ``(backend, model)`` (accepted privacy trade-off).
"""

import hashlib
import threading
from array import array
from collections import OrderedDict
from datetime import UTC, datetime

from sqlalchemy import Engine, bindparam, text
from sqlmodel import Session


def content_hash(embedded_text: str) -> str:
    return hashlib.sha256(embedded_text.encode()).hexdigest()


def pack_vector(vec: list[float]) -> bytes:
    """float32 little-endian blob (the layout sqlite-vec expects)."""
    return array("f", vec).tobytes()


def unpack_vector(blob: bytes) -> list[float]:
    a = array("f")
    a.frombytes(blob)
    return list(a)


class EmbeddingCacheRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def get_many(self, hashes: list[str], backend: str, model: str) -> dict[str, list[float]]:
        if not hashes:
            return {}
        stmt = text(
            "SELECT content_hash, embedding FROM embedding_cache"
            " WHERE backend = :backend AND model = :model AND content_hash IN :hashes"
        ).bindparams(bindparam("hashes", expanding=True))
        with Session(self._engine) as session:
            rows = session.execute(  # ty: ignore[deprecated] - raw SQL
                stmt, {"backend": backend, "model": model, "hashes": list(hashes)}
            ).fetchall()
        return {r.content_hash: unpack_vector(r.embedding) for r in rows}

    def put_many(self, entries: dict[str, list[float]], backend: str, model: str, dim: int) -> None:
        if not entries:
            return
        now = datetime.now(UTC).isoformat()
        stmt = text(
            "INSERT INTO embedding_cache"
            " (content_hash, backend, model, dim, embedding, created_at, last_used_at)"
            " VALUES (:h, :backend, :model, :dim, :emb, :now, :now)"
            " ON CONFLICT (content_hash, backend, model)"
            " DO UPDATE SET last_used_at = :now"
        )
        with Session(self._engine) as session:
            for h, vec in entries.items():
                session.execute(  # ty: ignore[deprecated] - raw SQL
                    stmt,
                    {
                        "h": h,
                        "backend": backend,
                        "model": model,
                        "dim": dim,
                        "emb": pack_vector(vec),
                        "now": now,
                    },
                )
            session.commit()


class QueryEmbeddingCache:
    """In-memory LRU of query embeddings keyed by ``(query, backend, model)``. Guarded
    by a lock so it is safe to share across threads under free-threaded Python."""

    def __init__(self, maxsize: int = 256):
        self._store: OrderedDict[tuple[str, str, str], list[float]] = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def get(self, query: str, backend: str, model: str) -> list[float] | None:
        key = (query, backend, model)
        with self._lock:
            if key not in self._store:
                return None
            self._store.move_to_end(key)
            return self._store[key]

    def put(self, query: str, backend: str, model: str, vec: list[float]) -> None:
        key = (query, backend, model)
        with self._lock:
            self._store[key] = vec
            self._store.move_to_end(key)
            while len(self._store) > self._maxsize:
                self._store.popitem(last=False)
