from kajet_turbo.cache import WorkspaceCache
from kajet_turbo.services.notes import NoteService


def test_epoch_starts_at_zero_and_bumps():
    c = WorkspaceCache()
    assert c.epoch("ws", "u") == 0
    c.bump("ws", "u")
    assert c.epoch("ws", "u") == 1
    assert c.epoch("other", "u") == 0


def test_get_put_roundtrip_and_miss():
    c = WorkspaceCache()
    assert c.get(("k",)) is None
    c.put(("k",), [1, 2])
    assert c.get(("k",)) == [1, 2]


def test_ttl_expiry():
    clock = [0.0]
    c = WorkspaceCache(ttl=10, timer=lambda: clock[0])
    c.put(("k",), 1)
    assert c.get(("k",)) == 1
    clock[0] = 11.0
    assert c.get(("k",)) is None


class FakeRepo:
    def __init__(self) -> None:
        self.calls = 0

    def hybrid_search(self, query, ws, owner_id, limit):
        self.calls += 1
        return [{"note_id": "n1", "title": "t"}]


def test_search_is_cached_and_epoch_invalidates():
    cache = WorkspaceCache()
    repo = FakeRepo()
    svc = NoteService(repo, cache=cache)

    r1 = svc.search("q", ["ws"], owner_id="u")
    r2 = svc.search("q", ["ws"], owner_id="u")
    assert r1 == r2
    assert repo.calls == 1  # second call served from cache

    cache.bump("ws", "u")
    svc.search("q", ["ws"], owner_id="u")
    assert repo.calls == 2  # epoch change = new key = recompute


def test_search_without_cache_always_hits_repo():
    repo = FakeRepo()
    svc = NoteService(repo, cache=None)
    svc.search("q", ["ws"], owner_id="u")
    svc.search("q", ["ws"], owner_id="u")
    assert repo.calls == 2
