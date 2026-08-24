"""Thread-safe in-memory TTL caches.

``TtlCache`` is the shared synchronized primitive. ``WorkspaceCache`` adds
per-workspace epochs: every workspace write bumps its epoch, making all old
entries unreachable at once (and eventually expiring them via TTL).

All caches are per-process. With multiple workers, TTL bounds staleness caused
by changes in sibling processes.
"""

import os
import threading
from collections.abc import Callable, Hashable
from typing import overload

from cachetools import TTLCache

from kajet_turbo.perf import incr


def cache_enabled() -> bool:
    return os.getenv("KAJET_CACHE", "1") != "0"


class TtlCache[K: Hashable, V]:
    """Small synchronized wrapper around cachetools' in-memory TTL cache.

    cachetools caches are deliberately not thread-safe. The application runs on
    free-threaded Python, so every shared cache must serialize access explicitly.
    """

    def __init__(
        self, maxsize: int = 2048, ttl: float = 300.0, timer: Callable[[], float] | None = None
    ) -> None:
        kwargs = {"timer": timer} if timer is not None else {}
        self._cache: TTLCache[K, V] = TTLCache(maxsize=maxsize, ttl=ttl, **kwargs)
        self._lock = threading.Lock()

    @overload
    def get(self, key: K) -> V | None: ...
    @overload
    def get[D](self, key: K, default: D) -> V | D: ...
    def get[D](self, key: K, default: D | None = None) -> V | D | None:
        """Cached value, or ``default``. Pass a sentinel to tell a miss from a
        stored ``None`` — callers that cache negative results need that."""
        with self._lock:
            return self._cache.get(key, default)

    def put(self, key: K, value: V) -> None:
        with self._lock:
            self._cache[key] = value


class WorkspaceCache:
    def __init__(
        self, maxsize: int = 2048, ttl: float = 300.0, timer: Callable[[], float] | None = None
    ) -> None:
        self._cache = TtlCache[tuple, object](maxsize=maxsize, ttl=ttl, timer=timer)
        self._epochs: dict[tuple[str, str], int] = {}
        # Guards _epochs only — the cache serializes itself inside TtlCache. Do not
        # take this around get/put: they would then hold two locks for one read.
        self._epoch_lock = threading.Lock()

    def epoch(self, ws_name: str, owner_id: str) -> int:
        with self._epoch_lock:
            return self._epochs.get((ws_name, owner_id), 0)

    def bump(self, ws_name: str, owner_id: str) -> None:
        with self._epoch_lock:
            key = (ws_name, owner_id)
            self._epochs[key] = self._epochs.get(key, 0) + 1

    def get(self, key: tuple):
        value = self._cache.get(key)
        incr("ws_cache_hit" if value is not None else "ws_cache_miss")
        return value

    def put(self, key: tuple, value) -> None:
        self._cache.put(key, value)
