import httpx

from kajet_turbo.embedding.client import SharedEmbedderClient


async def test_get_lazily_creates_and_reuses():
    holder = SharedEmbedderClient()
    client = holder.get()
    assert isinstance(client, httpx.AsyncClient)
    assert holder.get() is client  # same instance → connection pool is shared
    await holder.aclose()


async def test_aclose_is_idempotent_and_holder_reopens():
    holder = SharedEmbedderClient()
    first = holder.get()
    await holder.aclose()
    await holder.aclose()  # second close must not raise
    assert first.is_closed
    reopened = holder.get()  # lazy holder recreates after close
    assert reopened is not first
    assert not reopened.is_closed
    await holder.aclose()


async def test_aclose_without_get_is_noop():
    await SharedEmbedderClient().aclose()
