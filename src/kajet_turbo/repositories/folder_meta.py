from datetime import UTC, datetime

from sqlalchemy import Engine
from sqlmodel import Session, col, or_, select

from kajet_turbo.log import logger
from kajet_turbo.models import FolderMeta
from kajet_turbo.perf import timed


class FolderMetaRepository:
    """Per-(owner_id, workspace, path) folder metadata store.

    Partial upserts preserve unspecified fields (None = keep existing). rename_paths
    rewrites all nested paths in Python so move_folder never orphans metadata rows."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def set(
        self,
        owner_id: str,
        workspace: str,
        path: str,
        *,
        description: str | None = None,
        instructions: str | None = None,
    ) -> None:
        """Upsert folder metadata. None fields preserve existing values."""
        now = datetime.now(UTC).isoformat()
        with Session(self._engine) as session, timed("db_ms"):
            row = session.exec(
                select(FolderMeta).where(
                    FolderMeta.owner_id == owner_id,
                    FolderMeta.workspace == workspace,
                    FolderMeta.path == path,
                )
            ).first()
            if row is None:
                row = FolderMeta(
                    owner_id=owner_id,
                    workspace=workspace,
                    path=path,
                    description=description if description is not None else "",
                    instructions=instructions if instructions is not None else "",
                    updated_at=now,
                )
            else:
                if description is not None:
                    row.description = description
                if instructions is not None:
                    row.instructions = instructions
                row.updated_at = now
            session.add(row)
            session.commit()
        logger.info("folder_meta_set", owner_id=owner_id, ws=workspace, path=path)

    def get(self, owner_id: str, workspace: str, path: str) -> FolderMeta | None:
        with Session(self._engine) as session, timed("db_ms"):
            return session.exec(
                select(FolderMeta).where(
                    FolderMeta.owner_id == owner_id,
                    FolderMeta.workspace == workspace,
                    FolderMeta.path == path,
                )
            ).first()

    def get_many(self, owner_id: str, workspace: str, paths: list[str]) -> dict[str, FolderMeta]:
        if not paths:
            return {}
        with Session(self._engine) as session, timed("db_ms"):
            rows = session.exec(
                select(FolderMeta).where(
                    FolderMeta.owner_id == owner_id,
                    FolderMeta.workspace == workspace,
                    col(FolderMeta.path).in_(paths),
                )
            ).all()
        return {r.path: r for r in rows}

    def rename_paths(self, owner_id: str, workspace: str, src: str, dst: str) -> None:
        """Rename src folder and all descendants to dst, rewriting path prefix in-place.

        e.g. src='backlog', dst='archive/backlog' renames:
          'backlog'     -> 'archive/backlog'
          'backlog/sub' -> 'archive/backlog/sub'
        """
        if src == dst:
            return
        now = datetime.now(UTC).isoformat()
        with Session(self._engine) as session, timed("db_ms"):
            rows = session.exec(
                select(FolderMeta).where(
                    FolderMeta.owner_id == owner_id,
                    FolderMeta.workspace == workspace,
                    or_(
                        col(FolderMeta.path) == src,
                        col(FolderMeta.path).startswith(src + "/", autoescape=True),
                    ),
                )
            ).all()
            for row in rows:
                if row.path == src:
                    row.path = dst
                else:
                    row.path = dst + row.path[len(src) :]
                row.updated_at = now
                session.add(row)
            count = len(rows)
            session.commit()
        logger.info(
            "folder_meta_renamed",
            owner_id=owner_id,
            ws=workspace,
            src=src,
            dst=dst,
            count=count,
        )
