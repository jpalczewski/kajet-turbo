from sqlalchemy import delete
from sqlmodel import Session, col, select

from kajet_turbo.log import logger
from kajet_turbo.models import WorkspaceAccess
from kajet_turbo.repositories import DbRepository


class WorkspaceRepository(DbRepository):
    def grant_access(self, user_id: str, workspace: str, role: str = "owner") -> None:
        with Session(self._engine) as session:
            existing = session.exec(
                select(WorkspaceAccess).where(
                    WorkspaceAccess.user_id == user_id,
                    WorkspaceAccess.workspace == workspace,
                )
            ).first()
            if existing:
                return
            session.add(WorkspaceAccess(user_id=user_id, workspace=workspace, role=role))
            session.commit()

    def revoke_access(self, user_id: str, workspace: str) -> None:
        with self.timed_session() as session:
            session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
                delete(WorkspaceAccess).where(
                    col(WorkspaceAccess.user_id) == user_id,
                    col(WorkspaceAccess.workspace) == workspace,
                )
            )
            session.commit()
        logger.info("workspace_access_revoked", owner_id=user_id, ws=workspace)

    def list_user_workspaces(self, user_id: str) -> list[str]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(WorkspaceAccess)
                .where(WorkspaceAccess.user_id == user_id)
                .order_by(WorkspaceAccess.workspace)
            ).all()
        return [r.workspace for r in rows]

    def has_access(self, user_id: str, workspace: str) -> bool:
        with Session(self._engine) as session:
            return (
                session.exec(
                    select(WorkspaceAccess).where(
                        WorkspaceAccess.user_id == user_id,
                        WorkspaceAccess.workspace == workspace,
                    )
                ).first()
                is not None
            )
