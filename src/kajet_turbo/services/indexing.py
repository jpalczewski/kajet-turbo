"""Note indexing: two different contracts depending on batch size.

``index_note`` (single note) chunks and persists FTS inline on the request path — read-your-
writes for full-text search. The embedding HTTP roundtrip is NOT done here: when a backend
resolves, an ``embed_note`` job is enqueued and the worker attaches vectors out-of-band (see
``services/embed_handler.py``), flipping the note from ``stale`` to ``indexed``.

``index_many`` (batch/side-effect writes — ``save_notes``, ``edit_notes``, ``rename_tag``,
``reindex_workspace``, backlink rewrites) does no chunking at all: it enqueues one durable
``reindex_note`` job per note (see ``services/reindex_handler.py``), which does the chunk + FTS
write and chains into ``embed_note`` itself, off the request path entirely.

Best-effort contract: indexing NEVER raises to the caller. For ``index_note``, no backend /
resolve error ⇒ chunks are still written and the note stays ``index_state='stale'`` (FTS-only).
For ``index_many``, an enqueue failure is logged and swallowed — the affected notes simply stay
``stale`` until the next write re-enqueues them. Retry/backoff for the jobs themselves lives in
the job queue."""

from collections.abc import Callable, Iterable

from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.cache import EmbeddingCacheRepository, content_hash
from kajet_turbo.log import logger
from kajet_turbo.markdown import chunk_markdown, embedded_text
from kajet_turbo.perf import incr, timed
from kajet_turbo.repositories.jobs import PRIORITY_BULK, JobEntry, JobRepository
from kajet_turbo.repositories.notes import NoteChunkRepository


def reindex_job_entries(owner_id: str, workspace: str, note_ids: Iterable[str]) -> list[JobEntry]:
    """Build one ``reindex_note`` JobEntry per note id, with the dedup key ``ReindexNoteHandler``
    and every enqueue site (index_many, _rewrite_backlinks) agree on. Centralized so the payload
    shape and dedup-key format can't drift between the two call sites that build it."""
    return [
        JobEntry(
            payload={"owner_id": owner_id, "workspace": workspace, "note_id": note_id},
            dedup_key=f"reindex:{owner_id}:{workspace}:{note_id}",
            user_id=owner_id,
        )
        for note_id in note_ids
    ]


def safe_resolve_backend(
    resolve_backend: Callable[[str], EmbedderConfig | None], owner_id: str
) -> EmbedderConfig | None:
    """Resolve an owner's active embedding backend, tolerating resolve errors (e.g. SECRET_KEY
    unset → cipher refuses to build). Shared by NoteIndexer and ReindexNoteHandler so both
    degrade the same way: a resolve failure never breaks indexing, it just means no embed
    enqueue — the note stays FTS-only rather than losing already-written chunks."""
    try:
        return resolve_backend(owner_id)
    except Exception as e:
        logger.opt(exception=e).warning("index_resolve_failed", owner_id=owner_id)
        return None


class NoteIndexer:
    def __init__(
        self,
        repo: NoteChunkRepository,
        cache: EmbeddingCacheRepository,
        resolve_backend: Callable[[str], EmbedderConfig | None],
        jobs: JobRepository,
        *,
        enqueue_embed: Callable[[str, str, str], None] | None = None,
    ):
        self._repo = repo
        self._cache = cache
        self._resolve_backend = resolve_backend
        self._jobs = jobs
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
        return self._persist_chunks(
            note_id, workspace, owner_id, title, chunks, expected_generation=expected_generation
        )

    def _persist_chunks(
        self,
        note_id: str,
        workspace: str,
        owner_id: str,
        title: str,
        chunks: list,  # list[kajet_turbo.markdown.Chunk]
        *,
        expected_generation: int | None = None,
    ) -> bool:
        """Write already-chunked content (always vector-less → ``stale``). Returns True
        when the note produced chunks, i.e. there is something to embed."""
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
        # No active profile → None. A keyless profile (api_key is None) is a valid
        # local/no-auth endpoint and DOES embed — the adapter omits the auth header.
        return safe_resolve_backend(self._resolve_backend, owner_id)

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
        """Fan out a batch reindex as durable ``reindex_note`` jobs, one per note, in a
        single commit — no request-path chunking. The handler (``ReindexNoteHandler``) reads
        each note's file, chunks it, and chains into ``embed_note`` when a backend resolves.
        ``notes`` items need only ``id``.

        Best-effort like the rest of this module: enqueue failure (e.g. a DB hiccup) must
        not surface to callers that already committed the note rows via
        ``defer_workspace_postprocess`` — it is logged and swallowed. The affected notes stay
        ``index_state='stale'`` until the next save/reindex re-enqueues them."""
        if not notes:
            return
        try:
            entries = reindex_job_entries(owner_id, workspace, (note["id"] for note in notes))
            self._jobs.enqueue_many("reindex_note", entries, priority=PRIORITY_BULK)
        except Exception as e:
            logger.opt(exception=e).error(
                "reindex_enqueue_failed", workspace=workspace, owner_id=owner_id, count=len(notes)
            )
