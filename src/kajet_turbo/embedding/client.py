"""Per-process holder of a long-lived httpx.AsyncClient for query embedding.

Connection keep-alive across searches removes the TCP+TLS connect cost a fresh
per-call client pays on every embed (a visible fraction of the observed ~380ms
p50 roundtrip). Only the event loop thread touches the client, so there is no
loop-affinity problem; creation is lock-guarded anyway for 3.14t free threading.
``aclose`` is idempotent and the holder lazily re-creates after close.

The worker/indexing path keeps using ``_PooledEmbedder`` (fresh client per call):
it runs in per-call ``asyncio.run`` loops where a shared client cannot live, and
background latency doesn't matter there.
"""

import threading

import httpx


class SharedEmbedderClient:
    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._lock = threading.Lock()

    def get(self) -> httpx.AsyncClient:
        with self._lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(timeout=self._timeout)
            return self._client

    async def aclose(self) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is not None and not client.is_closed:
            await client.aclose()
