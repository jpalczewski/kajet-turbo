from sqlalchemy import delete
from sqlmodel import col, select

from kajet_turbo.models import WorkspaceAccess
from kajet_turbo.repositories import DbRepository


class WorkspaceRepository(DbRepository):
    repository_name = "workspaces"

    def grant_access(self, user_id: str, workspace: str, role: str = "owner") -> None:
        with self.operation(
            "grant_access", user_id=user_id, workspace=workspace, role=role
        ) as operation:
            session = operation.session
            existing = session.exec(
                select(WorkspaceAccess).where(
                    WorkspaceAccess.user_id == user_id,
                    WorkspaceAccess.workspace == workspace,
                )
            ).first()
            if existing:
                operation.suppress_log()
                return
            session.add(WorkspaceAccess(user_id=user_id, workspace=workspace, role=role))
            session.commit()

    def revoke_access(self, user_id: str, workspace: str) -> None:
        with self.operation("revoke_access", user_id=user_id, workspace=workspace) as operation:
            session = operation.session
            session.exec(
                delete(WorkspaceAccess).where(
                    col(WorkspaceAccess.user_id) == user_id,
                    col(WorkspaceAccess.workspace) == workspace,
                )
            )
            session.commit()

    def list_user_workspaces(self, user_id: str) -> list[str]:
        with self.timed_session() as session:
            rows = session.exec(
                select(WorkspaceAccess)
                .where(WorkspaceAccess.user_id == user_id)
                .order_by(WorkspaceAccess.workspace)
            ).all()
        return [r.workspace for r in rows]

    def has_access(self, user_id: str, workspace: str) -> bool:
        with self.timed_session() as session:
            return (
                session.exec(
                    select(WorkspaceAccess).where(
                        WorkspaceAccess.user_id == user_id,
                        WorkspaceAccess.workspace == workspace,
                    )
                ).first()
                is not None
            )
