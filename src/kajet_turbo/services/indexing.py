"""Note indexing: chunk a note, embed its chunks (best-effort, content-cache-bounded),
and persist chunks + vectors via the repository.

Embedding is async (httpx) but the service layer is sync and already runs in a worker
thread (via run_sync at the MCP/API boundary), so we drive the embedder with
``asyncio.run`` here — never ``run_sync`` (that is for sync-blocking offload, not async I/O).

Best-effort contract: indexing NEVER raises to the caller. No backend / no key / embedder
error ⇒ chunks are still written, vectors skipped, the note marked ``index_state='stale'``.
"""

import asyncio
from collections.abc import Callable

from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.cache import EmbeddingCacheRepository, content_hash
from kajet_turbo.log import logger
from kajet_turbo.markdown import chunk_markdown, embedded_text
from kajet_turbo.perf import incr, timed
from kajet_turbo.repositories.notes import NoteChunkRepository


class NoteIndexer:
    def __init__(
        self,
        repo: NoteChunkRepository,
        cache: EmbeddingCacheRepository,
        resolve_backend: Callable[[str], EmbedderConfig | None],
        build_embedder: Callable[[EmbedderConfig], object],
    ):
        self._repo = repo
        self._cache = cache
        self._resolve_backend = resolve_backend
        self._build_embedder = build_embedder

    def index_note(
        self, note_id: str, workspace: str, owner_id: str, title: str, content: str
    ) -> None:
        with timed("chunk_ms"):
            chunks = chunk_markdown(content, title=title)
        if not chunks:
            self._repo.replace_chunks(note_id, workspace, owner_id, title, [], None, None)
            return
        incr("chunks", len(chunks))

        try:
            cfg = self._resolve_backend(owner_id)
        except Exception:
            # Resolving the backend can fail (e.g. SECRET_KEY unset → cipher refuses to
            # build). That must not lose the chunks: degrade to stale, write chunks anyway.
            logger.warning("index_resolve_failed", note_id=note_id)
            cfg = None
        # No active profile → FTS-only (stale). A keyless profile (api_key is None) is a
        # valid local/no-auth endpoint and DOES embed — the adapter omits the auth header.
        if cfg is None:
            self._repo.replace_chunks(note_id, workspace, owner_id, title, chunks, None, None)
            return

        try:
            embeddings = self._embed_chunks(cfg, chunks)
        except Exception:
            logger.warning("index_embed_failed", note_id=note_id, backend=cfg.backend_id)
            self._repo.replace_chunks(note_id, workspace, owner_id, title, chunks, None, None)
            return

        self._repo.ensure_vec_table(cfg.dim)
        self._repo.replace_chunks(note_id, workspace, owner_id, title, chunks, embeddings, cfg.dim)
        # Records the user's active (backend, model, dim). If the user later switches to a
        # different-dim backend, notes that are never re-written keep vectors only in the old
        # dim shard and silently fall back to FTS-only at search time. Detecting that drift
        # here and reindexing into the new shard is a planned follow-up (backend-switch
        # reindex); for now a manual reindex_workspace after a switch repopulates them.
        self._repo.upsert_index_meta(owner_id, cfg.backend_id, cfg.model, cfg.dim)

    def preview(self, title: str, content: str, owner_id: str) -> list[dict]:
        """Live re-chunk of ``content`` with per-chunk 'embedded?' flags (a content-cache
        hash lookup against the owner's resolved backend; no network, no stored rows). When
        no backend resolves, every chunk reports embedded=False."""
        chunks = chunk_markdown(content, title=title)
        if not chunks:
            return []
        texts = [embedded_text(c) for c in chunks]
        hashes = [content_hash(t) for t in texts]
        cached: dict[str, list[float]] = {}
        try:
            cfg = self._resolve_backend(owner_id)
        except Exception:
            cfg = None
        if cfg is not None:
            cached = self._cache.get_many(hashes, cfg.backend_id, cfg.model)
        return [
            {
                "ordinal": c.ordinal,
                "header_path": list(c.header_path),
                "content": c.content,
                "embedded_text": texts[i],
                "char_start": c.char_start,
                "char_end": c.char_end,
                # body length — the metric the chunk-size thresholds are tuned against;
                # the embedded text (breadcrumb + body) is exposed separately as embedded_text.
                "char_count": len(c.content),
                "embedded": hashes[i] in cached,
            }
            for i, c in enumerate(chunks)
        ]

    def index_many(self, workspace: str, owner_id: str, notes: list[dict]) -> None:
        """Reindex a batch of notes. Chunking (pure CPU) parallelizes across threads under
        free-threading; each note is embedded best-effort. A single note's failure is
        logged and skipped — it never aborts the batch. ``notes`` items need ``id``,
        ``title``, ``content``."""
        from concurrent.futures import ThreadPoolExecutor

        # Pre-create the owner's dim-sharded vec table once (idempotent) so the worker
        # threads don't race the CREATE-VIRTUAL-TABLE-IF-NOT-EXISTS existence check during
        # fan-out. Best-effort: if no backend resolves, each note still indexes FTS-only.
        try:
            cfg = self._resolve_backend(owner_id)
            if cfg is not None:  # keyless profiles embed too (adapter omits the auth header)
                self._repo.ensure_vec_table(cfg.dim)
        except Exception:
            logger.warning("reindex_prepare_vec_failed", owner_id=owner_id)

        def _one(note: dict) -> None:
            try:
                self.index_note(
                    note["id"],
                    workspace,
                    owner_id,
                    note.get("title") or "",
                    note.get("content") or "",
                )
            except Exception:
                logger.warning("reindex_note_failed", note_id=note.get("id"))

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_one, notes))

    def clear_note(self, note_id: str) -> None:
        """Drop a note's chunks + vectors (best-effort). Used before deleting the note row."""
        self._repo.replace_chunks(note_id, "", "", "", [], None, None)

    def _embed_chunks(self, cfg: EmbedderConfig, chunks: list) -> list[list[float]]:
        """One vector per chunk, hitting the embedder only for cache-misses."""
        texts = [embedded_text(c) for c in chunks]
        hashes = [content_hash(t) for t in texts]
        cached = self._cache.get_many(hashes, cfg.backend_id, cfg.model)

        miss_idx = [i for i, h in enumerate(hashes) if h not in cached]
        incr("embed_cache_hits", len(hashes) - len(miss_idx))
        incr("embed_cache_misses", len(miss_idx))
        if miss_idx:
            embedder = self._build_embedder(cfg)
            miss_vectors = asyncio.run(
                embedder.embed_documents([texts[i] for i in miss_idx])  # ty: ignore[unresolved-attribute]  # duck-typed embedder seam
            )
            new_entries = {hashes[i]: vec for i, vec in zip(miss_idx, miss_vectors, strict=True)}
            self._cache.put_many(new_entries, cfg.backend_id, cfg.model, cfg.dim)
            cached = {**cached, **new_entries}

        return [cached[h] for h in hashes]
