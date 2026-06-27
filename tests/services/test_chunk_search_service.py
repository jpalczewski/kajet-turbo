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
        build_embedder=lambda c: None,
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
    assert set(h) >= {"note_id", "title", "header_path", "content", "score"}
    assert "tomato" in h["content"]
    assert h["header_path"][0] == "# Recipes"
    assert h["score"] is not None  # numeric score even in FTS-only mode


def test_search_empty_when_no_match(database, git_workspace_factory):
    service = _service(database)
    ws = git_workspace_factory("ws")
    service.save("u1", "ws", str(ws), "Recipes", "# Recipes\n\ntomato soup\n", tags=[])
    assert service.search("zzzznomatch", ["ws"], owner_id="u1", limit=10) == []


def test_search_cache_key_varies_by_backend(database, git_workspace_factory):
    # A backend/key change must not keep serving the previous backend's cached ranking.
    chunk_repo = NoteChunkRepository(database.engine)
    indexer = NoteIndexer(
        chunk_repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda o: None,
        build_embedder=lambda c: None,
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
