import pytest

from kajet_turbo.embedding.cache import (
    EmbeddingCacheRepository,
    QueryEmbeddingCache,
    content_hash,
    pack_vector,
    unpack_vector,
)


def test_content_hash_is_deterministic_and_text_sensitive():
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")
    assert len(content_hash("abc")) == 64  # sha256 hex


def test_pack_unpack_round_trip():
    vec = [0.5, -1.25, 3.0]
    out = unpack_vector(pack_vector(vec))
    assert out == pytest.approx(vec)


def test_put_then_get_returns_vectors_scoped_by_backend_model(database):
    cache = EmbeddingCacheRepository(database.engine)
    h1, h2 = content_hash("one"), content_hash("two")
    cache.put_many({h1: [1.0, 2.0], h2: [3.0, 4.0]}, "openai", "m-large", dim=2)

    got = cache.get_many([h1, h2], "openai", "m-large")
    assert got[h1] == pytest.approx([1.0, 2.0])
    assert got[h2] == pytest.approx([3.0, 4.0])

    # different model → cache miss (no false reuse on backend switch)
    assert cache.get_many([h1], "openai", "m-small") == {}


def test_get_many_skips_unknown_hashes(database):
    cache = EmbeddingCacheRepository(database.engine)
    h = content_hash("present")
    cache.put_many({h: [0.0]}, "openai", "m", dim=1)
    got = cache.get_many([h, content_hash("absent")], "openai", "m")
    assert set(got) == {h}


def test_get_many_empty_input(database):
    cache = EmbeddingCacheRepository(database.engine)
    assert cache.get_many([], "openai", "m") == {}


def test_put_many_is_idempotent(database):
    cache = EmbeddingCacheRepository(database.engine)
    h = content_hash("dup")
    cache.put_many({h: [1.0]}, "openai", "m", dim=1)
    cache.put_many({h: [1.0]}, "openai", "m", dim=1)  # ON CONFLICT → no error
    assert cache.get_many([h], "openai", "m")[h] == pytest.approx([1.0])


def test_query_lru_get_put_and_eviction():
    lru = QueryEmbeddingCache(maxsize=2)
    assert lru.get("q1", "openai", "m") is None
    lru.put("q1", "openai", "m", [1.0])
    lru.put("q2", "openai", "m", [2.0])
    assert lru.get("q1", "openai", "m") == [1.0]  # touch q1 → most-recent
    lru.put("q3", "openai", "m", [3.0])  # evicts q2 (least-recent)
    assert lru.get("q2", "openai", "m") is None
    assert lru.get("q1", "openai", "m") == [1.0]
    assert lru.get("q3", "openai", "m") == [3.0]


def test_query_lru_keyed_by_backend_model():
    lru = QueryEmbeddingCache(maxsize=4)
    lru.put("q", "openai", "m1", [1.0])
    assert lru.get("q", "openai", "m2") is None
