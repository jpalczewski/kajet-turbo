"""Repository for per-workspace push remotes. Owner-scoped, keyed (user_id,
workspace). The DB is the source of truth for push configuration and status."""

from datetime import UTC, datetime

from sqlalchemy import Engine
from sqlmodel import Session

from kajet_turbo.models import WorkspaceRemote


def _now() -> str:
    return datetime.now(UTC).isoformat()


class WorkspaceRemoteRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def get(self, user_id: str, workspace: str) -> WorkspaceRemote | None:
        with Session(self._engine) as session:
            return session.get(WorkspaceRemote, (user_id, workspace))

    def upsert(
        self,
        user_id: str,
        workspace: str,
        *,
        origin_url: str,
        ssh_key_id: str,
        enabled: bool,
        now: str | None = None,
    ) -> WorkspaceRemote:
        now = now or _now()
        with Session(self._engine) as session:
            row = session.get(WorkspaceRemote, (user_id, workspace))
            if row is None:
                row = WorkspaceRemote(
                    user_id=user_id,
                    workspace=workspace,
                    origin_url=origin_url,
                    ssh_key_id=ssh_key_id,
                    enabled=enabled,
                    updated_at=now,
                )
            else:
                row.origin_url = origin_url
                row.ssh_key_id = ssh_key_id
                row.enabled = enabled
                row.updated_at = now
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def delete(self, user_id: str, workspace: str) -> bool:
        with Session(self._engine) as session:
            row = session.get(WorkspaceRemote, (user_id, workspace))
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def mark_dirty(self, user_id: str, workspace: str, *, now: str | None = None) -> None:
        self._patch(user_id, workspace, dirty_at=now or _now())

    def mark_pushed(self, user_id: str, workspace: str, *, now: str | None = None) -> None:
        self._patch(user_id, workspace, pushed_at=now or _now(), last_error=None)

    def mark_failed(
        self, user_id: str, workspace: str, error: str, *, now: str | None = None
    ) -> None:
        self._patch(user_id, workspace, last_error=error)

    def _patch(self, user_id: str, workspace: str, **fields) -> None:
        with Session(self._engine) as session:
            row = session.get(WorkspaceRemote, (user_id, workspace))
            if row is None:
                return
            for k, v in fields.items():
                setattr(row, k, v)
            session.add(row)
            session.commit()
