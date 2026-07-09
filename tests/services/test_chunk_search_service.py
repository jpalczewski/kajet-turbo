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
        "u1", "ws", str(ws), "Rozmowa 3 marca", "", tags=["angelika"], folder="książki/Angelika"
    )
    hits = service.search("angelika", ["ws"], owner_id="u1", limit=10)
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
    service.save("u1", "ws", str(ws), "Early angelika note", "", tags=["angelika"], folder="a")
    service.save("u1", "ws", str(ws), "Late angelika note", "", tags=["angelika"], folder="b")
    hits = service.search("angelika", ["ws"], owner_id="u1", limit=1, folder="a")
    assert [h["title"] for h in hits] == ["Early angelika note"]


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
