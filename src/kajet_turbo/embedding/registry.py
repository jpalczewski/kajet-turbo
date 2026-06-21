"""Instance backend registry: the set of embedding backends an instance offers,
parsed from env. Non-secret definitions only — per-user key/selection live in the DB.

Env:
  EMBEDDING_BACKENDS         JSON object mapping backend_id -> {type, model, dim,
                             base_url, query_prefix?, passage_prefix?}
  EMBEDDING_DEFAULT_BACKEND  backend_id used when a user has no selection
                             (falls back to the first declared backend if unset)
"""

import json
import os
from dataclasses import dataclass

from kajet_turbo.embedding.base import EmbedderConfig


@dataclass(frozen=True)
class BackendDef:
    backend_id: str
    type: str
    model: str
    dim: int
    base_url: str
    query_prefix: str = ""
    passage_prefix: str = ""

    def to_config(self, api_key: str | None) -> EmbedderConfig:
        return EmbedderConfig(
            backend_id=self.backend_id,
            type=self.type,
            model=self.model,
            dim=self.dim,
            base_url=self.base_url,
            query_prefix=self.query_prefix,
            passage_prefix=self.passage_prefix,
            api_key=api_key,
        )


@dataclass(frozen=True)
class Registry:
    backends: dict[str, BackendDef]
    default_id: str | None

    def get(self, backend_id: str | None) -> BackendDef | None:
        if backend_id and backend_id in self.backends:
            return self.backends[backend_id]
        if self.default_id and self.default_id in self.backends:
            return self.backends[self.default_id]
        return None


def load_registry(env: dict[str, str] | None = None) -> Registry:
    env = env if env is not None else dict(os.environ)
    raw = env.get("EMBEDDING_BACKENDS", "").strip()
    backends: dict[str, BackendDef] = {}
    if raw:
        for backend_id, d in json.loads(raw).items():
            backends[backend_id] = BackendDef(
                backend_id=backend_id,
                type=d["type"],
                model=d["model"],
                dim=int(d["dim"]),
                base_url=d["base_url"],
                query_prefix=d.get("query_prefix", ""),
                passage_prefix=d.get("passage_prefix", ""),
            )
    default_id = env.get("EMBEDDING_DEFAULT_BACKEND") or next(iter(backends), None)
    return Registry(backends=backends, default_id=default_id)
