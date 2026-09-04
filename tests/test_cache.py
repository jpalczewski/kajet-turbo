from kajet_turbo.cache import TtlCache


def test_ttl_cache_roundtrip_and_expiry():
    clock = [0.0]
    cache = TtlCache[str, str](ttl=10, timer=lambda: clock[0])

    assert cache.get("credential") is None
    cache.put("credential", "user-1")
    assert cache.get("credential") == "user-1"
    clock[0] = 11.0
    assert cache.get("credential") is None


def test_ttl_cache_default_separates_miss_from_stored_none():
    """Negative caching needs 'absent' and 'known to be nobody' to be different
    answers — with a bare get() both read as None."""
    missing = object()
    clock = [0.0]
    cache = TtlCache[str, str | None](ttl=10, timer=lambda: clock[0])

    assert cache.get("credential", missing) is missing
    cache.put("credential", None)
    assert cache.get("credential", missing) is None
    clock[0] = 11.0
    assert cache.get("credential", missing) is missing
