"""Deferred-embedding job handler (kind ``embed_note``).

Embeds a note's STORED chunk rows — no file I/O, no re-chunking: the write path
already persisted chunks + FTS inline, so a coalesced job always embeds the note's
current content. Embedder errors PROPAGATE — the worker turns them into retry with
backoff, replacing the old inline swallow-to-stale behavior. Everything else that
makes the job moot (note deleted, backend removed, chunks replaced by a concurrent
edit) is a quiet no-op: the note stays ``stale`` and the responsible follow-up job
or manual reindex repairs it.

The service layer is sync (worker thread pool), so the async embedder is driven
with ``asyncio.run`` — same bridge the inline path used.
"""

import asyncio
import json
from collections.abc import Callable

from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.cache import EmbeddingCacheRepository, content_hash
from kajet_turbo.log import logger
from kajet_turbo.markdown import Chunk, embedded_text
from kajet_turbo.perf import incr
from kajet_turbo.repositories.notes import NoteChunkRepository


class EmbedNoteHandler:
    def __init__(
        self,
        chunk_repo: NoteChunkRepository,
        cache: EmbeddingCacheRepository,
        resolve_backend: Callable[[str], EmbedderConfig | None],
        build_embedder: Callable[[EmbedderConfig], object],
    ):
        self._repo = chunk_repo
        self._cache = cache
        self._resolve_backend = resolve_backend
        self._build_embedder = build_embedder

    def __call__(self, payload: dict) -> None:
        note_id = payload["note_id"]
        workspace = payload["workspace"]
        owner_id = payload["owner_id"]

        rows = self._repo.get_chunks(note_id)
        if not rows:
            logger.info("embed_note_skipped", note_id=note_id, reason="no_chunks")
            return
        cfg = self._resolve_backend(owner_id)
        if cfg is None:
            logger.info("embed_note_skipped", note_id=note_id, reason="no_backend")
            return

        texts = [
            embedded_text(
                Chunk(
                    ordinal=r["ordinal"],
                    header_path=json.loads(r["header_path"]),
                    content=r["content"],
                    char_start=r["char_start"],
                    char_end=r["char_end"],
                )
            )
            for r in rows
        ]
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

        self._repo.ensure_vec_table(cfg.dim)
        vectors = {r["id"]: cached[h] for r, h in zip(rows, hashes, strict=True)}
        applied = self._repo.attach_vectors(note_id, workspace, owner_id, cfg.dim, vectors)
        if applied:
            self._repo.upsert_index_meta(owner_id, cfg.backend_id, cfg.model, cfg.dim)
        else:
            # Chunk set drifted (concurrent edit) — the edit's own follow-up job is
            # already pending, so completing here is correct, not a failure.
            logger.info("embed_superseded", note_id=note_id)
