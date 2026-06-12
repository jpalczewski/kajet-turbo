"""Thread-safe TTL cache with per-workspace epochs.

Every write to a workspace bumps its epoch; the epoch is part of every cache
key, so all entries for that workspace become unreachable at once (and expire
via TTL). No explicit invalidation, no races.

Limitation (by design, see spec): per-process. With MCP_WORKERS>1 each process
caches independently; TTL bounds staleness caused by writes in sibling
processes.
"""
import os
import threading
from collections.abc import Callable

from cachetools import TTLCache


def cache_enabled() -> bool:
    return os.getenv("KAJET_CACHE", "1") != "0"


class WorkspaceCache:
    def __init__(self, maxsize: int = 2048, ttl: float = 300.0,
                 timer: Callable[[], float] | None = None) -> None:
        kwargs = {"timer": timer} if timer is not None else {}
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl, **kwargs)
        self._epochs: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def epoch(self, ws_name: str, owner_id: str) -> int:
        with self._lock:
            return self._epochs.get((ws_name, owner_id), 0)

    def bump(self, ws_name: str, owner_id: str) -> None:
        with self._lock:
            key = (ws_name, owner_id)
            self._epochs[key] = self._epochs.get(key, 0) + 1

    def get(self, key: tuple):
        with self._lock:
            return self._cache.get(key)

    def put(self, key: tuple, value) -> None:
        with self._lock:
            self._cache[key] = value
