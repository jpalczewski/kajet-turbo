"""Thread-safe in-memory TTL caches.

``TtlCache`` is the shared synchronized primitive — used directly by ``log.py``'s
error-dedup and user-id lookup caches. All caches are per-process; with multiple
workers, TTL bounds staleness caused by changes in sibling processes.
"""

import os
import threading
from collections.abc import Callable, Hashable
from typing import overload

from cachetools import TTLCache


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
