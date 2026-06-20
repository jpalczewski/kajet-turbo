"""Repository for per-user embedding backend selection + sealed API key.

``upsert`` is a full replace of ``(backend_id, api_key_enc)``; the write-only API in
Plan 5 decides any merge semantics (e.g. preserving an existing key)."""

from datetime import UTC, datetime

from sqlalchemy import Engine
from sqlmodel import Session

from kajet_turbo.models import UserEmbeddingConfig


class EmbeddingConfigRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def get(self, user_id: str) -> UserEmbeddingConfig | None:
        with Session(self._engine) as session:
            return session.get(UserEmbeddingConfig, user_id)

    def upsert(self, user_id: str, backend_id: str | None, api_key_enc: bytes | None) -> None:
        now = datetime.now(UTC).isoformat()
        with Session(self._engine) as session:
            row = session.get(UserEmbeddingConfig, user_id)
            if row is None:
                row = UserEmbeddingConfig(
                    user_id=user_id,
                    backend_id=backend_id,
                    api_key_enc=api_key_enc,
                    created_at=now,
                    updated_at=now,
                )
            else:
                row.backend_id = backend_id
                row.api_key_enc = api_key_enc
                row.updated_at = now
            session.add(row)
            session.commit()
