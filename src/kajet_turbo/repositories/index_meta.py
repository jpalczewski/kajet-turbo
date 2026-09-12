from datetime import UTC, datetime

from kajet_turbo.models import IndexMeta
from kajet_turbo.repositories import DbRepository


class IndexMetaRepository(DbRepository):
    """Persistence for the active embedding-index identity of each user."""

    repository_name = "index_meta"

    def get(self, owner_id: str) -> IndexMeta | None:
        with self.timed_session() as session:
            return session.get(IndexMeta, owner_id)

    def upsert(self, owner_id: str, backend: str, model: str, dim: int) -> None:
        now = datetime.now(UTC).isoformat()
        with self.operation("upsert", owner_id=owner_id, model=model, dim=dim) as operation:
            session = operation.session
            row = session.get(IndexMeta, owner_id)
            if row is None:
                session.add(
                    IndexMeta(
                        owner_id=owner_id,
                        backend=backend,
                        model=model,
                        dim=dim,
                        updated_at=now,
                    )
                )
            else:
                row.backend = backend
                row.model = model
                row.dim = dim
                row.updated_at = now
            session.commit()
