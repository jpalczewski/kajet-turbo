import asyncio

from kajet_turbo.cache import WorkspaceCache
from kajet_turbo.embedding.cache import pack_vector
from kajet_turbo.log import logger
from kajet_turbo.repositories.notes import NoteChunkRepository


class NoteSearchService:
    def __init__(
        self,
        chunk_repo: NoteChunkRepository,
        cache: WorkspaceCache | None,
        query_resolver,
        build_embedder,
        query_cache,
    ):
        self._chunk_repo = chunk_repo
        self._cache = cache
        self._query_resolver = query_resolver
        self._build_embedder = build_embedder
        self._query_cache = query_cache

    def search(
        self,
        query: str,
        workspaces: list[str],
        owner_id: str,
        limit: int = 10,
    ) -> list[dict]:
        # Resolve the backend identity up front so it is part of the cache key: a config
        # change (backend switch / key add) must not keep serving the old backend's ranking
        # from cache. resolve is a cheap indexed read, fine to run on cache hits too.
        cfg = None
        if self._query_resolver is not None:
            try:
                cfg = self._query_resolver(owner_id)
            except Exception:
                cfg = None
        # An active profile (even keyless — a local/no-auth endpoint) drives vector search.
        embeddable = cfg is not None
        backend_key = (cfg.backend_id, cfg.dim) if embeddable else None

        key = None
        if self._cache is not None:
            epochs = tuple(self._cache.epoch(ws, owner_id) for ws in workspaces)
            key = ("search", owner_id, tuple(workspaces), epochs, query, limit, backend_key)
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        embedding = None
        dim = None
        if embeddable:
            try:
                vec = self._embed_query(cfg, query)
                embedding = pack_vector(vec)
                dim = cfg.dim
            except Exception:
                logger.warning("search_embed_failed", backend=cfg.backend_id)
        per_ws_limit = limit * 3 if len(workspaces) > 1 else limit
        results = []
        for ws in workspaces:
            hits = self._chunk_repo.hybrid_search(
                query, ws, owner_id, embedding=embedding, dim=dim, limit=per_ws_limit
            )
            results.extend(hits)
        results = results[:limit]
        if self._cache is not None and key is not None:
            self._cache.put(key, results)
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
