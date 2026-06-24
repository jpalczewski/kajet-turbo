from datetime import UTC, datetime

from sqlalchemy import Engine, text
from sqlmodel import Session, select

from kajet_turbo.models import ActiveWorkspace


class ActiveWorkspaceRepository:
    """Per-user active workspace store (one row per user).

    Used as the cross-session fallback for `get_active_workspace`: the claude.ai
    connector opens a fresh MCP session per tool call, so the in-memory session
    state is empty on the next call. Keying by the stable user id bridges that gap.
    """

    def __init__(self, engine: Engine):
        self._engine = engine

    def set(self, user_id: str, workspace: str) -> None:
        # INSERT OR REPLACE: atomic single-statement upsert on the PK, avoids the
        # TOCTOU window of select-then-update under free-threading.
        now = datetime.now(UTC).isoformat()
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "INSERT OR REPLACE INTO active_workspaces (user_id, workspace, updated_at)"
                    " VALUES (:user_id, :workspace, :updated_at)"
                ),
                {"user_id": user_id, "workspace": workspace, "updated_at": now},
            )
            session.commit()

    def get(self, user_id: str) -> str | None:
        with Session(self._engine) as session:
            row = session.exec(
                select(ActiveWorkspace).where(ActiveWorkspace.user_id == user_id)
            ).first()
        return row.workspace if row else None
