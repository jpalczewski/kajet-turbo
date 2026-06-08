from sqlalchemy import Engine
from sqlmodel import Session, select

from kajet_turbo.models import WorkspaceAccess


class WorkspaceRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

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
