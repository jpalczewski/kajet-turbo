"""Note indexing: chunk a note, persist chunks + FTS inline, and defer embedding to
the job queue.

Chunks + FTS are the reliable, cheap backbone written on the request path (read-your-
writes for full-text search). The embedding HTTP roundtrip is NOT done here anymore:
when a backend resolves, an ``embed_note`` job is enqueued and the worker attaches
vectors out-of-band (see ``services/embed_handler.py``), flipping the note from
``stale`` to ``indexed``. Retry/backoff lives in the job queue.

Best-effort contract: indexing NEVER raises to the caller. No backend / resolve error
⇒ chunks are still written and the note stays ``index_state='stale'`` (FTS-only)."""

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
        *,
        enqueue_embed: Callable[[str, str, str], None] | None = None,
    ):
        self._repo = repo
        self._cache = cache
        self._resolve_backend = resolve_backend
        self._enqueue_embed = enqueue_embed

    def index_note(
        self,
        note_id: str,
        workspace: str,
        owner_id: str,
        title: str,
        content: str,
        *,
        expected_generation: int | None = None,
    ) -> None:
        if not self._write_chunks(
            note_id,
            workspace,
            owner_id,
            title,
            content,
            expected_generation=expected_generation,
        ):
            return
        if self._enqueue_embed is not None and self._resolve_cfg(owner_id) is not None:
            self._enqueue_embed(note_id, workspace, owner_id)

    def _write_chunks(
        self,
        note_id: str,
        workspace: str,
        owner_id: str,
        title: str,
        content: str,
        *,
        expected_generation: int | None = None,
    ) -> bool:
        """Chunk and persist (always vector-less → ``stale``). Returns True when the
        note produced chunks, i.e. there is something to embed."""
        with timed("chunk_ms"):
            chunks = chunk_markdown(content, title=title)
        applied = self._repo.replace_chunks(
            note_id,
            workspace,
            owner_id,
            title,
            chunks,
            None,
            None,
            expected_generation=expected_generation,
        )
        if not applied:
            incr("index_superseded")
            logger.info("index_superseded", note_id=note_id)
            return False
        if chunks:
            incr("chunks", len(chunks))
        return bool(chunks)

    def _resolve_cfg(self, owner_id: str) -> EmbedderConfig | None:
        # Resolving the backend can fail (e.g. SECRET_KEY unset → cipher refuses to
        # build). That must not break indexing: no enqueue, note stays FTS-only.
        # No active profile → None. A keyless profile (api_key is None) is a valid
        # local/no-auth endpoint and DOES embed — the adapter omits the auth header.
        try:
            return self._resolve_backend(owner_id)
        except Exception as e:
            logger.opt(exception=e).warning("index_resolve_failed", owner_id=owner_id)
            return None

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
        cfg = self._resolve_cfg(owner_id)
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
        free-threading; each chunked note enqueues its own ``embed_note`` job (per-note
        retry, per-note dedup). The backend is resolved once for the whole batch. A single
        note's failure is logged and skipped — it never aborts the batch. ``notes`` items
        need ``id``, ``title``, ``content``."""
        from concurrent.futures import ThreadPoolExecutor

        embeddable = self._enqueue_embed is not None and self._resolve_cfg(owner_id) is not None

        def _one(note: dict) -> None:
            try:
                note_id = note["id"]
                has_chunks = self._write_chunks(
                    note_id,
                    workspace,
                    owner_id,
                    note.get("title") or "",
                    note.get("content") or "",
                    expected_generation=note.get("index_generation"),
                )
                if has_chunks and embeddable:
                    assert self._enqueue_embed is not None  # narrowed by `embeddable`
                    self._enqueue_embed(note_id, workspace, owner_id)
            except Exception as e:
                logger.opt(exception=e).warning("reindex_note_failed", note_id=note.get("id"))

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_one, notes))

    def clear_note(self, note_id: str) -> None:
        """Drop a note's chunks + vectors (best-effort). Used before deleting the note row."""
        self._repo.replace_chunks(note_id, "", "", "", [], None, None)
