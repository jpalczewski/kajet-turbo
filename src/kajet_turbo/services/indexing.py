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

from kajet_turbo.chunking import chunk_markdown, embedded_text
from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.cache import EmbeddingCacheRepository, content_hash
from kajet_turbo.log import logger
from kajet_turbo.repositories.notes import NoteRepository


class NoteIndexer:
    def __init__(
        self,
        repo: NoteRepository,
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
        chunks = chunk_markdown(content, title=title)
        if not chunks:
            self._repo.replace_chunks(note_id, workspace, owner_id, title, [], None, None)
            return

        try:
            cfg = self._resolve_backend(owner_id)
        except Exception:
            # Resolving the backend can fail (e.g. SECRET_KEY unset → cipher refuses to
            # build). That must not lose the chunks: degrade to stale, write chunks anyway.
            logger.warning("index_resolve_failed", note_id=note_id)
            cfg = None
        if cfg is None or cfg.api_key is None:
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
        self._repo.upsert_index_meta(owner_id, cfg.backend_id, cfg.model, cfg.dim)

    def index_many(self, workspace: str, owner_id: str, notes: list[dict]) -> None:
        """Reindex a batch of notes. Chunking (pure CPU) parallelizes across threads under
        free-threading; each note is embedded best-effort. A single note's failure is
        logged and skipped — it never aborts the batch. ``notes`` items need ``id``,
        ``title``, ``content``."""
        from concurrent.futures import ThreadPoolExecutor

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
        if miss_idx:
            embedder = self._build_embedder(cfg)
            miss_vectors = asyncio.run(
                embedder.embed_documents([texts[i] for i in miss_idx])  # ty: ignore[unresolved-attribute]  # duck-typed embedder seam
            )
            new_entries = {hashes[i]: vec for i, vec in zip(miss_idx, miss_vectors, strict=True)}
            self._cache.put_many(new_entries, cfg.backend_id, cfg.model, cfg.dim)
            cached = {**cached, **new_entries}

        return [cached[h] for h in hashes]
