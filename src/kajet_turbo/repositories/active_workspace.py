from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlmodel import Session, col, select

from kajet_turbo.log import logger
from kajet_turbo.models import ActiveWorkspace
from kajet_turbo.repositories import DbRepository


class ActiveWorkspaceRepository(DbRepository):
    """Persist active workspace by user and MCP context scope."""

    def set(self, user_id: str, workspace: str, scope: str = "user") -> None:
        now = datetime.now(UTC).isoformat()
        with Session(self._engine) as session:
            row = session.exec(
                select(ActiveWorkspace).where(
                    ActiveWorkspace.user_id == user_id,
                    ActiveWorkspace.scope == scope,
                )
            ).first()
            if row is None:
                row = ActiveWorkspace(
                    user_id=user_id,
                    scope=scope,
                    workspace=workspace,
                    updated_at=now,
                )
            else:
                row.workspace = workspace
                row.updated_at = now
            session.add(row)
            session.commit()

    def get(
        self, user_id: str, scope: str = "user", max_age: timedelta | None = None
    ) -> str | None:
        with Session(self._engine) as session:
            row = session.exec(
                select(ActiveWorkspace).where(
                    ActiveWorkspace.user_id == user_id,
                    ActiveWorkspace.scope == scope,
                )
            ).first()
        if row is None:
            return None
        if (
            max_age is not None
            and datetime.now(UTC) - datetime.fromisoformat(row.updated_at) > max_age
        ):
            return None
        return row.workspace

    def delete_for_workspace(self, user_id: str, workspace: str) -> None:
        """Clear any active-workspace pointer (any scope) for a deleted workspace."""
        with self.timed_session() as session:
            session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
                delete(ActiveWorkspace).where(
                    col(ActiveWorkspace.user_id) == user_id,
                    col(ActiveWorkspace.workspace) == workspace,
                )
            )
            session.commit()
        logger.info("active_workspace_deleted_for_workspace", owner_id=user_id, ws=workspace)
