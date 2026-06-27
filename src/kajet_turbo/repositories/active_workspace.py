from datetime import UTC, datetime

from sqlalchemy import Engine
from sqlmodel import Session, select

from kajet_turbo.models import ActiveWorkspace


class ActiveWorkspaceRepository:
    """Persist active workspace by user and MCP context scope."""

    def __init__(self, engine: Engine):
        self._engine = engine

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

    def get(self, user_id: str, scope: str = "user") -> str | None:
        with Session(self._engine) as session:
            row = session.exec(
                select(ActiveWorkspace).where(
                    ActiveWorkspace.user_id == user_id,
                    ActiveWorkspace.scope == scope,
                )
            ).first()
        return row.workspace if row else None
