from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.cache import EmbeddingCacheRepository, content_hash
from kajet_turbo.markdown import chunk_markdown, embedded_text
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.indexing import NoteIndexer


def _cfg():
    return EmbedderConfig(
        backend_id="b", type="openai", model="m", dim=3, base_url="http://x", api_key="k"
    )


def test_preview_marks_embedded_from_cache(database):
    repo = NoteRepository(database.engine)
    cache = EmbeddingCacheRepository(database.engine)
    indexer = NoteIndexer(
        repo, cache, resolve_backend=lambda o: _cfg(), build_embedder=lambda c: None
    )
    # Bodies are sized above the small-section merge threshold so the two sections stay
    # as separate chunks (a tiny "alpha body / beta body" pair would merge into one).
    body1 = "alpha " * 60
    body2 = "beta " * 60
    text = f"# Title\n\n{body1}\n\n## Sec\n\n{body2}\n"
    chunks = chunk_markdown(text, title="Title")
    assert len(chunks) >= 2
    first_hash = content_hash(embedded_text(chunks[0]))
    cache.put_many({first_hash: [0.0, 0.0, 0.0]}, "b", "m", 3)

    preview = indexer.preview("Title", text, "u1")
    assert len(preview) == len(chunks)
    assert preview[0]["embedded"] is True
    assert preview[0]["header_path"] == chunks[0].header_path
    assert preview[0]["embedded_text"] == embedded_text(chunks[0])
    assert preview[0]["char_count"] == len(chunks[0].content)
    assert preview[-1]["embedded"] is False


def test_preview_no_backend_all_false(database):
    repo = NoteRepository(database.engine)
    cache = EmbeddingCacheRepository(database.engine)
    indexer = NoteIndexer(
        repo, cache, resolve_backend=lambda o: None, build_embedder=lambda c: None
    )
    preview = indexer.preview("T", "# T\n\nbody\n", "u1")
    assert preview and all(p["embedded"] is False for p in preview)


def test_preview_empty_content(database):
    repo = NoteRepository(database.engine)
    cache = EmbeddingCacheRepository(database.engine)
    indexer = NoteIndexer(
        repo, cache, resolve_backend=lambda o: None, build_embedder=lambda c: None
    )
    assert indexer.preview("T", "   \n", "u1") == []
