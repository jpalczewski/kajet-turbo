from kajet_turbo.embedding.cache import EmbeddingCacheRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.indexing import NoteIndexer
from kajet_turbo.services.notes import NoteService


def _service(database):
    repo = NoteRepository(database.engine)
    indexer = NoteIndexer(
        repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda o: None,
        build_embedder=lambda c: None,
    )
    return NoteService(repo, indexer=indexer)


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
