import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from kajet_turbo.concurrency import run_sync
from kajet_turbo.embedding.base import (
    Embedder,
    EmbedderConfig,
    EmbeddingAuthError,
    EmbeddingRequestRejected,
)
from kajet_turbo.embedding.cache import pack_vector
from kajet_turbo.embedding.identity import IndexIdentity
from kajet_turbo.log import logger
from kajet_turbo.repositories.notes import (
    ChunkHit,
    NoteChunkRepository,
    NoteRepository,
    NoteTagRepository,
)
from kajet_turbo.services.notes.fusion import (
    DEFAULT_CANDIDATE_LIMIT,
    NARROWED_CANDIDATE_LIMIT,
    fuse_hybrid,
)
from kajet_turbo.workspace import folder_scope

# Why semantic ranking was dropped for one search. "auth_failed": the backend rejected the
# API key (401/403). "misconfigured": it rejected the request itself (400/404/422 — unknown
# model, wrong base URL), so a retry cannot help either. "unavailable": anything else —
# timeout, 5xx, 429, or the backend config could not be resolved — worth a retry.
type DegradedReason = Literal["auth_failed", "misconfigured", "unavailable"]
type SearchMode = Literal["hybrid", "keyword_only"]


@dataclass(frozen=True, slots=True)
class QueryVector:
    """The query embedding search ranks against, or why there is none.

    ``degraded_reason`` is None both when the embedding succeeded and when no backend is
    configured: keyword-only search by configuration is not a degradation."""

    embedding: bytes | None = None
    identity: IndexIdentity | None = None
    degraded_reason: DegradedReason | None = None

    @classmethod
    def embedded(cls, vector: list[float], cfg: EmbedderConfig) -> QueryVector:
        return cls(embedding=pack_vector(vector), identity=IndexIdentity.from_config(cfg))


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    results: list[ChunkHit]
    search_mode: SearchMode
    degraded_reason: DegradedReason | None
    # True when more hits ranked below the limit; the recourse is a larger limit, not
    # paging — deep offsets into an RRF ranking are not worth their cost.
    has_more: bool


def degraded_reason_for(exc: BaseException) -> DegradedReason:
    match exc:
        case EmbeddingAuthError():
            return "auth_failed"
        case EmbeddingRequestRejected():
            return "misconfigured"
        case _:
            return "unavailable"


class NoteSearchService:
    def __init__(
        self,
        chunk_repo: NoteChunkRepository,
        query_resolver,
        build_embedder,
        query_cache,
        crud_repo: NoteRepository,
        tag_repo: NoteTagRepository,
        async_build_embedder: Callable[[EmbedderConfig], Embedder] | None = None,
    ):
        self._chunk_repo = chunk_repo
        self._query_resolver = query_resolver
        self._build_embedder = build_embedder
        self._query_cache = query_cache
        self._crud_repo = crud_repo
        self._tag_repo = tag_repo
        self._async_build_embedder = async_build_embedder

    def search(
        self,
        query: str,
        workspaces: list[str],
        owner_id: str,
        limit: int = 10,
        folder: str | None = None,
        tags: list[str] | None = None,
    ) -> SearchOutcome:
        """Sync search: runs entirely on the calling (worker) thread, driving the
        embedder with ``asyncio.run``. The MCP boundary uses ``search_async`` instead
        so the query-embedding HTTP roundtrip doesn't pin a run_sync slot."""
        folder = folder_scope(folder)
        prepared = self._prepare(owner_id)
        match prepared:
            case EmbedderConfig() as cfg:
                try:
                    vector = QueryVector.embedded(self._embed_query(cfg, query), cfg)
                except Exception as e:
                    vector = self._embed_failed(cfg, e)
            case _:
                vector = prepared
        return self._execute(query, workspaces, owner_id, limit, folder, tags, vector)

    async def search_async(
        self,
        query: str,
        workspaces: list[str],
        owner_id: str,
        limit: int = 10,
        folder: str | None = None,
        tags: list[str] | None = None,
    ) -> SearchOutcome:
        """Async search: DB phases (_prepare/_execute) borrow a run_sync slot only for
        ms-scale work, while the query-embedding HTTP call is awaited natively on the
        event loop through the shared client — a slow embedding endpoint no longer
        occupies a limiter slot for its whole roundtrip."""
        folder = folder_scope(folder)  # fail fast, before any embedding call
        if self._async_build_embedder is None:
            # No async embedder wired (test doubles / legacy wiring): run the whole
            # sync search in one worker-thread slot, as before.
            return await run_sync(self.search, query, workspaces, owner_id, limit, folder, tags)
        prepared = await run_sync(self._prepare, owner_id)
        match prepared:
            case EmbedderConfig() as cfg:
                try:
                    vector = QueryVector.embedded(await self._embed_query_async(cfg, query), cfg)
                except Exception as e:
                    vector = self._embed_failed(cfg, e)
            case _:
                vector = prepared
        return await run_sync(
            self._execute, query, workspaces, owner_id, limit, folder, tags, vector
        )

    @staticmethod
    def _embed_failed(cfg: EmbedderConfig, exc: Exception) -> QueryVector:
        """Search degrades to keyword-only when the query can't be embedded. A rejected
        key or a rejected request is a persistent misconfiguration, not a blip, so it gets
        an ERROR record; transient failures stay a warning."""
        reason = degraded_reason_for(exc)
        match exc:
            case EmbeddingAuthError():
                logger.error(
                    "embedding_auth_failed",
                    backend=cfg.backend_id,
                    status_code=exc.status_code,
                    degraded_to="fts",
                )
            case EmbeddingRequestRejected():
                logger.error(
                    "embedding_request_rejected",
                    backend=cfg.backend_id,
                    status_code=exc.status_code,
                    degraded_to="fts",
                )
            case _:
                logger.opt(exception=exc).warning(
                    "search_embed_failed", backend=cfg.backend_id, degraded_reason=reason
                )
        return QueryVector(degraded_reason=reason)

    def _prepare(self, owner_id: str) -> EmbedderConfig | QueryVector:
        """Resolve the active embedding backend. Sync — cheap indexed DB read. Without a
        backend the search is keyword-only by configuration; a failed resolution is a
        degradation, not "no backend", so it carries a reason."""
        if self._query_resolver is None:
            return QueryVector()
        try:
            cfg = self._query_resolver(owner_id)
        except Exception as e:
            logger.opt(exception=e).warning("search_resolve_failed", owner_id=owner_id)
            return QueryVector(degraded_reason="unavailable")
        return QueryVector() if cfg is None else cfg

    def _execute(
        self,
        query: str,
        workspaces: list[str],
        owner_id: str,
        limit: int,
        folder: str | None,
        tags: list[str] | None,
        vector: QueryVector,
    ) -> SearchOutcome:
        """Narrow, fetch candidates, fuse them, and log. Sync DB work."""
        # One hit past the limit is what tells has_more apart from an exact fit.
        per_ws_limit = (limit * 3 if len(workspaces) > 1 else limit) + 1
        results: list[ChunkHit] = []
        for ws in workspaces:
            allowed: set[str] | None = None
            if tags:
                allowed = self._tag_repo.note_ids_for_tags(
                    ws, owner_id, tags, include_descendants=True
                )
            if folder is not None:
                folder_ids = self._crud_repo.note_ids_under_folder(ws, owner_id, folder)
                allowed = folder_ids if allowed is None else allowed & folder_ids
            if allowed is not None and not allowed:
                continue
            # When narrowing is active, widen every candidate window before filtering — otherwise
            # an in-scope metadata match
            # ranked below per_ws_limit globally gets truncated here before allowed_note_ids
            # ever filters it in.
            candidate_limit = (
                NARROWED_CANDIDATE_LIMIT if allowed is not None else DEFAULT_CANDIDATE_LIMIT
            )
            meta_limit = candidate_limit if allowed is not None else per_ws_limit
            meta_hits = self._crud_repo.search_metadata(ws, owner_id, query, limit=meta_limit)
            fts = self._chunk_repo.search_fts(query, ws, owner_id, limit=candidate_limit)
            vec = (
                self._chunk_repo.search_chunks_vec(
                    vector.embedding, ws, owner_id, identity=vector.identity, k=candidate_limit
                )
                if vector.embedding is not None and vector.identity is not None
                else []
            )
            hits = fuse_hybrid(
                fts,
                vec,
                meta_hits,
                limit=per_ws_limit,
                allowed_note_ids=allowed,
            )
            results.extend(hits)
        # score is an RRF rank within each workspace, not a globally calibrated relevance
        # signal — but sorting by it beats leaving results in arbitrary workspace-iteration
        # order. No-op for the single-workspace case (fuse_hybrid already returns sorted).
        results.sort(key=lambda result: result.score, reverse=True)
        has_more = len(results) > limit
        results = results[:limit]
        search_mode: SearchMode = "hybrid" if vector.embedding is not None else "keyword_only"
        logger.info(
            "search_performed",
            query_len=len(query),
            results=len(results),
            ws_count=len(workspaces),
            search_mode=search_mode,
            degraded_reason=vector.degraded_reason,
        )
        return SearchOutcome(
            results=results,
            search_mode=search_mode,
            degraded_reason=vector.degraded_reason,
            has_more=has_more,
        )

    def _embed_query(self, cfg, query: str) -> list[float]:
        if self._query_cache is not None:
            cached = self._query_cache.get(query, cfg.backend_id, cfg.model)
            if cached is not None:
                return cached
        # Only reached when search() resolved a backend, which is wired together with
        # build_embedder in the DI container; the None default is for cache-only test doubles.
        embedder = self._build_embedder(cfg)
        vec = asyncio.run(embedder.embed_query(query))
        if self._query_cache is not None:
            self._query_cache.put(query, cfg.backend_id, cfg.model, vec)
        return vec

    async def _embed_query_async(self, cfg, query: str) -> list[float]:
        if self._query_cache is not None:
            cached = self._query_cache.get(query, cfg.backend_id, cfg.model)
            if cached is not None:
                return cached
        assert self._async_build_embedder is not None  # guarded by search_async
        embedder = self._async_build_embedder(cfg)
        vec = await embedder.embed_query(query)
        if self._query_cache is not None:
            self._query_cache.put(query, cfg.backend_id, cfg.model, vec)
        return vec
