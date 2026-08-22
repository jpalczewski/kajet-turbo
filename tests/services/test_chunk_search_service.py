from kajet_turbo.cache import WorkspaceCache
from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.cache import EmbeddingCacheRepository
from kajet_turbo.repositories.notes import NoteChunkRepository
from kajet_turbo.services.indexing import NoteIndexer
from tests.services.conftest import build_note_service


def _service(database):
    chunk_repo = NoteChunkRepository(database.engine)
    indexer = NoteIndexer(
        chunk_repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda o: None,
    )
    return build_note_service(database, indexer=indexer)


def test_search_returns_chunk_shape_fts_only(database, git_workspace_factory):
    service = _service(database)
    ws = git_workspace_factory("ws")
    service.save(
        "u1", "ws", str(ws), "Recipes", "# Recipes\n\n## Soup\n\ntomato basil soup\n", tags=[]
    )
    hits = service.search("tomato", ["ws"], owner_id="u1", limit=10)
    assert len(hits) >= 1
    h = hits[0]
    assert set(h) >= {"note_id", "title", "header_path", "content", "score", "updated_at"}
    assert "tomato" in h["content"]
    assert h["header_path"][0] == "# Recipes"
    assert h["score"] is not None  # numeric score even in FTS-only mode


def test_search_empty_when_no_match(database, git_workspace_factory):
    service = _service(database)
    ws = git_workspace_factory("ws")
    service.save("u1", "ws", str(ws), "Recipes", "# Recipes\n\ntomato soup\n", tags=[])
    assert service.search("zzzznomatch", ["ws"], owner_id="u1", limit=10) == []


def test_search_matches_tag_and_folder_for_contentless_note(database, git_workspace_factory):
    service = _service(database)
    ws = git_workspace_factory("ws")
    service.save(
        "u1", "ws", str(ws), "Rozmowa 3 marca", "", tags=["alice"], folder="książki/Alice"
    )
    hits = service.search("alice", ["ws"], owner_id="u1", limit=10)
    assert len(hits) == 1
    assert set(hits[0]["matched_on"]) == {"folder", "tag"}
    assert hits[0]["content"] == ""
    assert hits[0]["header_path"] == []


def test_search_matches_title_of_contentless_note(database, git_workspace_factory):
    service = _service(database)
    ws = git_workspace_factory("ws")
    service.save("u1", "ws", str(ws), "Unikalny Tytul Beztresciowy", "", tags=[])
    hits = service.search("Unikalny Tytul Beztresciowy", ["ws"], owner_id="u1", limit=10)
    assert len(hits) == 1
    assert hits[0]["matched_on"] == ["title"]


def test_search_cache_key_varies_by_backend(database, git_workspace_factory):
    # A backend/key change must not keep serving the previous backend's cached ranking.
    chunk_repo = NoteChunkRepository(database.engine)
    indexer = NoteIndexer(
        chunk_repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda o: None,
    )

    calls = {"n": 0}
    inner = chunk_repo.hybrid_search

    def counting(*a, **k):
        calls["n"] += 1
        return inner(*a, **k)

    chunk_repo.hybrid_search = counting  # type: ignore[method-assign]  # ty: ignore[invalid-assignment] - patch spy for cache-key regression

    class _FakeEmbedder:
        async def embed_query(self, text):
            return [1.0, 0.0, 0.0]

    state: dict = {"cfg": None}
    svc = build_note_service(
        database,
        indexer=indexer,
        cache=WorkspaceCache(),
        query_resolver=lambda o: state["cfg"],
        build_embedder=lambda c: _FakeEmbedder(),
        chunk_repo=chunk_repo,  # pass the patched repo through
    )
    ws = git_workspace_factory("ws")
    svc.save("u1", "ws", str(ws), "T", "# T\n\nalpha\n", tags=[])

    svc.search("alpha", ["ws"], owner_id="u1")
    assert calls["n"] == 1
    svc.search("alpha", ["ws"], owner_id="u1")
    assert calls["n"] == 1  # same backend → cache hit, no recompute

    # Switch backend → cache key differs → recompute (no crash even though no vectors at dim 3)
    state["cfg"] = EmbedderConfig(
        backend_id="b2", type="openai", model="m", dim=3, base_url="http://x", api_key="k"
    )
    svc.search("alpha", ["ws"], owner_id="u1")
    assert calls["n"] == 2


def test_search_across_workspaces_sorts_by_score_globally(database, git_workspace_factory):
    # Iteration order is ["ws1", "ws2"], but the higher-scored hit lives in ws2 (title match
    # boosts its score above ws1's content-only match) — a global top-k must still surface it
    # even though the buggy code (concat-then-truncate, no cross-workspace sort) would keep
    # whatever landed first from ws1 instead.
    service = _service(database)
    ws1 = git_workspace_factory("ws1")
    ws2 = git_workspace_factory("ws2")
    service.save(
        "u1", "ws1", str(ws1), "Alpha document", "# Alpha document\n\nfindmequery here\n", tags=[]
    )
    service.save(
        "u1", "ws2", str(ws2), "findmequery", "# findmequery\n\nfindmequery here too\n", tags=[]
    )
    hits = service.search("findmequery", ["ws1", "ws2"], owner_id="u1", limit=1)
    assert len(hits) == 1
    assert hits[0]["title"] == "findmequery"


def test_search_narrows_by_folder(database, git_workspace_factory):
    service = _service(database)
    ws = git_workspace_factory("ws")
    service.save("u1", "ws", str(ws), "In scope", "keyword here", tags=[], folder="a")
    service.save("u1", "ws", str(ws), "Out of scope", "keyword here", tags=[], folder="b")
    hits = service.search("keyword", ["ws"], owner_id="u1", limit=10, folder="a")
    assert [h["title"] for h in hits] == ["In scope"]


def test_search_narrows_by_folder_widens_metadata_candidate_window(database, git_workspace_factory):
    # search_metadata's own ranking (tiebreak: updated_at desc) would put the later-saved
    # "b" note ahead of the earlier "a" note. With limit=1 and folder narrowing to "a", the
    # metadata call must fetch a wide-enough window that the in-scope "a" note survives the
    # allowed_note_ids filter in hybrid_search, rather than being truncated out beforehand.
    service = _service(database)
    ws = git_workspace_factory("ws")
    service.save("u1", "ws", str(ws), "Early alice note", "", tags=["alice"], folder="a")
    service.save("u1", "ws", str(ws), "Late alice note", "", tags=["alice"], folder="b")
    hits = service.search("alice", ["ws"], owner_id="u1", limit=1, folder="a")
    assert [h["title"] for h in hits] == ["Early alice note"]


def test_search_narrows_by_tags(database, git_workspace_factory):
    service = _service(database)
    ws = git_workspace_factory("ws")
    service.save("u1", "ws", str(ws), "Tagged", "keyword here", tags=["work"])
    service.save("u1", "ws", str(ws), "Untagged", "keyword here", tags=[])
    hits = service.search("keyword", ["ws"], owner_id="u1", limit=10, tags=["work"])
    assert [h["title"] for h in hits] == ["Tagged"]


def test_search_folder_and_tags_intersect(database, git_workspace_factory):
    service = _service(database)
    ws = git_workspace_factory("ws")
    service.save("u1", "ws", str(ws), "Both", "keyword here", tags=["work"], folder="a")
    service.save("u1", "ws", str(ws), "Only folder", "keyword here", tags=[], folder="a")
    service.save("u1", "ws", str(ws), "Only tag", "keyword here", tags=["work"], folder="b")
    hits = service.search("keyword", ["ws"], owner_id="u1", limit=10, folder="a", tags=["work"])
    assert [h["title"] for h in hits] == ["Both"]


def test_search_cache_invalidated_when_deferred_embed_lands(database, git_workspace_factory):
    # save → search caches an FTS-only ranking (note still 'stale'); when the worker
    # attaches vectors (stale → indexed), the next search must recompute instead of
    # serving the vector-less ranking until the cache TTL expires.
    chunk_repo = NoteChunkRepository(database.engine)
    indexer = NoteIndexer(
        chunk_repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda o: None,
    )

    calls = {"n": 0}
    inner = chunk_repo.hybrid_search

    def counting(*a, **k):
        calls["n"] += 1
        return inner(*a, **k)

    chunk_repo.hybrid_search = counting  # type: ignore[method-assign]  # ty: ignore[invalid-assignment] - patch spy for cache-key regression

    class _FakeEmbedder:
        async def embed_query(self, text):
            return [1.0, 0.0, 0.0]

    cfg = EmbedderConfig(
        backend_id="b", type="openai", model="m", dim=3, base_url="http://x", api_key="k"
    )
    svc = build_note_service(
        database,
        indexer=indexer,
        cache=WorkspaceCache(),
        query_resolver=lambda o: cfg,
        build_embedder=lambda c: _FakeEmbedder(),
        chunk_repo=chunk_repo,
    )
    ws = git_workspace_factory("ws")
    res = svc.save("u1", "ws", str(ws), "T", "# T\n\nalpha\n", tags=[])

    svc.search("alpha", ["ws"], owner_id="u1")
    assert calls["n"] == 1
    svc.search("alpha", ["ws"], owner_id="u1")
    assert calls["n"] == 1  # cached

    # Worker attaches vectors out-of-band: stale → indexed, no epoch bump.
    chunk_repo.ensure_vec_table(3)
    rows = chunk_repo.get_chunks(res["note_id"])
    applied = chunk_repo.attach_vectors(
        res["note_id"], "ws", "u1", 3, {r["id"]: [1.0, 0.0, 0.0] for r in rows}
    )
    assert applied is True

    svc.search("alpha", ["ws"], owner_id="u1")
    assert calls["n"] == 2  # stale-count changed → cache key changed → recompute


class _AsyncCountingEmbedder:
    def __init__(self):
        self.calls = 0

    async def embed_query(self, text):
        self.calls += 1
        return [1.0, 0.0, 0.0]


def _async_service(database, chunk_repo=None, *, cache=None, query_cache=None):
    """Service wired for async query embedding; the sync build_embedder seam raises so
    a regression back to the run_sync-slot path is loud."""
    if chunk_repo is None:
        chunk_repo = NoteChunkRepository(database.engine)
    indexer = NoteIndexer(
        chunk_repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda o: None,
    )
    cfg = EmbedderConfig(
        backend_id="b", type="openai", model="m", dim=3, base_url="http://x", api_key="k"
    )
    emb = _AsyncCountingEmbedder()

    def _sync_seam_must_not_be_used(c):
        raise AssertionError("sync build_embedder used on the async path")

    svc = build_note_service(
        database,
        indexer=indexer,
        cache=cache,
        query_resolver=lambda o: cfg,
        build_embedder=_sync_seam_must_not_be_used,
        query_cache=query_cache,
        chunk_repo=chunk_repo,
        async_build_embedder=lambda c: emb,
    )
    return svc, emb


async def test_search_async_matches_sync_shape(database, git_workspace_factory):
    svc, _emb = _async_service(database)
    ws = git_workspace_factory("ws")
    svc.save("u1", "ws", str(ws), "Recipes", "# Recipes\n\ntomato basil soup\n", tags=[])
    hits = await svc.search_async("tomato", ["ws"], owner_id="u1", limit=10)
    assert len(hits) >= 1
    assert set(hits[0]) >= {"note_id", "title", "header_path", "content", "score", "updated_at"}


async def test_search_async_embeds_query_on_event_loop(database, git_workspace_factory):
    svc, emb = _async_service(database)
    ws = git_workspace_factory("ws")
    svc.save("u1", "ws", str(ws), "T", "# T\n\nalpha\n", tags=[])
    await svc.search_async("alpha", ["ws"], owner_id="u1")
    assert emb.calls == 1


async def test_search_async_result_cache_hit_skips_embed(database, git_workspace_factory):
    svc, emb = _async_service(database, cache=WorkspaceCache())
    ws = git_workspace_factory("ws")
    svc.save("u1", "ws", str(ws), "T", "# T\n\nalpha\n", tags=[])
    await svc.search_async("alpha", ["ws"], owner_id="u1")
    await svc.search_async("alpha", ["ws"], owner_id="u1")
    assert emb.calls == 1  # second search served from the result cache


async def test_search_async_query_cache_hit_skips_embedder(database, git_workspace_factory):
    from kajet_turbo.embedding.cache import QueryEmbeddingCache

    svc, emb = _async_service(database, query_cache=QueryEmbeddingCache())
    ws = git_workspace_factory("ws")
    svc.save("u1", "ws", str(ws), "T", "# T\n\nalpha\n", tags=[])
    # No result cache; a different limit cannot be served from it anyway — only the
    # query-embedding LRU can dedupe the embed call.
    await svc.search_async("alpha", ["ws"], owner_id="u1", limit=5)
    await svc.search_async("alpha", ["ws"], owner_id="u1", limit=7)
    assert emb.calls == 1


async def test_search_async_embed_failure_degrades_to_fts(database, git_workspace_factory):
    svc, emb = _async_service(database)

    async def _boom(text):
        raise RuntimeError("embedding endpoint down")

    emb.embed_query = _boom  # type: ignore[method-assign]
    ws = git_workspace_factory("ws")
    svc.save("u1", "ws", str(ws), "T", "# T\n\nalpha\n", tags=[])
    hits = await svc.search_async("alpha", ["ws"], owner_id="u1")
    assert len(hits) >= 1  # FTS still answers


async def test_search_async_falls_back_to_sync_without_async_embedder(
    database, git_workspace_factory
):
    # Test doubles / legacy wiring without async_build_embedder keep working: the
    # whole search runs through the sync path in a worker thread.
    service = _service(database)
    ws = git_workspace_factory("ws")
    service.save("u1", "ws", str(ws), "Recipes", "# Recipes\n\ntomato soup\n", tags=[])
    hits = await service.search_async("tomato", ["ws"], owner_id="u1", limit=10)
    assert len(hits) >= 1
