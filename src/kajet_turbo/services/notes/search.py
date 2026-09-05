import asyncio
from collections.abc import Callable

from kajet_turbo.concurrency import run_sync
from kajet_turbo.embedding.base import Embedder, EmbedderConfig
from kajet_turbo.embedding.cache import pack_vector
from kajet_turbo.embedding.identity import IndexIdentity
from kajet_turbo.log import logger
from kajet_turbo.repositories.notes import NoteChunkRepository, NoteRepository, NoteTagRepository


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
    ) -> list[dict]:
        """Sync search: runs entirely on the calling (worker) thread, driving the
        embedder with ``asyncio.run``. The MCP boundary uses ``search_async`` instead
        so the query-embedding HTTP roundtrip doesn't pin a run_sync slot."""
        cfg = self._prepare(owner_id)
        embedding = None
        identity = None
        if cfg is not None:
            try:
                vec = self._embed_query(cfg, query)
                embedding = pack_vector(vec)
                identity = IndexIdentity.from_config(cfg)
            except Exception as e:
                logger.opt(exception=e).warning("search_embed_failed", backend=cfg.backend_id)
        return self._execute(query, workspaces, owner_id, limit, folder, tags, embedding, identity)

    async def search_async(
        self,
        query: str,
        workspaces: list[str],
        owner_id: str,
        limit: int = 10,
        folder: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        """Async search: DB phases (_prepare/_execute) borrow a run_sync slot only for
        ms-scale work, while the query-embedding HTTP call is awaited natively on the
        event loop through the shared client — a slow embedding endpoint no longer
        occupies a limiter slot for its whole roundtrip."""
        if self._async_build_embedder is None:
            # No async embedder wired (test doubles / legacy wiring): run the whole
            # sync search in one worker-thread slot, as before.
            return await run_sync(self.search, query, workspaces, owner_id, limit, folder, tags)
        cfg = await run_sync(self._prepare, owner_id)
        embedding = None
        identity = None
        if cfg is not None:
            try:
                vec = await self._embed_query_async(cfg, query)
                embedding = pack_vector(vec)
                identity = IndexIdentity.from_config(cfg)
            except Exception as e:
                logger.opt(exception=e).warning("search_embed_failed", backend=cfg.backend_id)
        return await run_sync(
            self._execute, query, workspaces, owner_id, limit, folder, tags, embedding, identity
        )

    def _prepare(self, owner_id: str) -> EmbedderConfig | None:
        """Resolve the active embedding backend, if any. Sync — cheap indexed DB read."""
        if self._query_resolver is None:
            return None
        try:
            return self._query_resolver(owner_id)
        except Exception as e:
            logger.opt(exception=e).warning("search_resolve_failed", owner_id=owner_id)
            return None

    def _execute(
        self,
        query: str,
        workspaces: list[str],
        owner_id: str,
        limit: int,
        folder: str | None,
        tags: list[str] | None,
        embedding: bytes | None,
        identity: IndexIdentity | None,
    ) -> list[dict]:
        """Narrow, fuse (hybrid_search), and log. Sync DB work."""
        per_ws_limit = limit * 3 if len(workspaces) > 1 else limit
        results = []
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
            # When narrowing is active, widen the metadata window to match hybrid_search's own
            # candidate_limit (200) for narrowed searches — otherwise an in-scope metadata match
            # ranked below per_ws_limit globally gets truncated here before allowed_note_ids
            # ever filters it in.
            meta_limit = 200 if allowed is not None else per_ws_limit
            meta_hits = self._crud_repo.search_metadata(ws, owner_id, query, limit=meta_limit)
            hits = self._chunk_repo.hybrid_search(
                query,
                ws,
                owner_id,
                embedding=embedding,
                identity=identity,
                limit=per_ws_limit,
                meta_hits=meta_hits,
                allowed_note_ids=allowed,
            )
            results.extend(hits)
        # score is an RRF rank within each workspace, not a globally calibrated relevance
        # signal — but sorting by it beats leaving results in arbitrary workspace-iteration
        # order. No-op for the single-workspace case (hybrid_search already returns sorted).
        results.sort(key=lambda r: r["score"], reverse=True)
        results = results[:limit]
        logger.info(
            "search_performed", query_len=len(query), results=len(results), ws_count=len(workspaces)
        )
        return results

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
