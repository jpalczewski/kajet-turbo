"""IndexIdentity: which vector space a stored embedding belongs to.

Embeddings from two models are not comparable, so every vector carries the identity it
was produced under and KNN only ever scans one. The identity is ``(backend, model)`` --
``backend`` is the endpoint (``EmbedderConfig.backend_id``), because local servers
(Ollama, TEI, vLLM) expose generic model names and ``model`` alone would merge two
unrelated spaces. ``dim`` rides along: it selects the table shard rather than being part
of the key.
"""

import hashlib
from dataclasses import dataclass

from kajet_turbo.embedding.base import EmbedderConfig

# 64 bits of SHA-256. Collisions are irrelevant at this cardinality (one identity per
# distinct endpoint+model a user has ever indexed with); the length keeps the partition
# value small, since vec0 stores it once per chunk block.
_KEY_CHARS = 16


@dataclass(frozen=True)
class IndexIdentity:
    """The vector space a set of embeddings lives in.

    ``key`` is PERSISTED as a vec0 partition value, so the digest is part of the storage
    format: changing how it is derived orphans every vector already written under it and
    requires a migration, not just a redeploy.
    """

    backend: str
    model: str
    dim: int

    @classmethod
    def from_config(cls, config: EmbedderConfig) -> IndexIdentity:
        return cls(backend=config.backend_id, model=config.model, dim=config.dim)

    @property
    def key(self) -> str:
        # NUL separator: neither a URL nor a model name can contain it, so no pair of
        # distinct identities can collide by concatenation.
        raw = f"{self.backend}\x00{self.model}".encode()
        return hashlib.sha256(raw).hexdigest()[:_KEY_CHARS]
