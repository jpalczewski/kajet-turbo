import json
from datetime import UTC, datetime

from sqlalchemy import delete, text
from sqlmodel import col, select

from kajet_turbo.models import WorkspaceMeta
from kajet_turbo.repositories import DbRepository


def _row_to_dict(row: WorkspaceMeta) -> dict:
    return {
        "description": row.description,
        "folder": row.folder,
        "tags": json.loads(row.tags or "[]"),
    }


class WorkspaceMetaRepository(DbRepository):
    """Per-(user, workspace) metadata store. Partial upserts via SQLite
    ON CONFLICT so unspecified fields are preserved."""

    repository_name = "workspace_meta"

    def ensure(self, user_id: str, workspace: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.operation("ensure", user_id=user_id, workspace=workspace) as operation:
            session = operation.session
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "INSERT INTO workspace_meta (user_id, workspace, description, folder, tags,"
                    " updated_at) VALUES (:u, :w, '', '', NULL, :now)"
                    " ON CONFLICT(user_id, workspace) DO NOTHING"
                ),
                {"u": user_id, "w": workspace, "now": now},
            )
            session.commit()

    def set(
        self,
        user_id: str,
        workspace: str,
        *,
        description: str | None = None,
        folder: str | None = None,
        tags: str | None = None,
    ) -> None:
        # COALESCE(:val, existing) gives partial-update semantics: None leaves the
        # column unchanged, a value overwrites it. On insert the COALESCE falls back
        # to the VALUES literal so a brand-new row gets defaults for omitted fields.
        now = datetime.now(UTC).isoformat()
        with self.operation("set", user_id=user_id, workspace=workspace) as operation:
            session = operation.session
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "INSERT INTO workspace_meta"
                    " (user_id, workspace, description, folder, tags, updated_at)"
                    " VALUES (:u, :w, COALESCE(:d, ''), COALESCE(:f, ''), :t, :now)"
                    " ON CONFLICT(user_id, workspace) DO UPDATE SET"
                    "   description = COALESCE(:d, workspace_meta.description),"
                    "   folder      = COALESCE(:f, workspace_meta.folder),"
                    "   tags        = COALESCE(:t, workspace_meta.tags),"
                    "   updated_at  = :now"
                ),
                {
                    "u": user_id,
                    "w": workspace,
                    "d": description,
                    "f": folder,
                    "t": tags,
                    "now": now,
                },
            )
            session.commit()

    def get(self, user_id: str, workspace: str) -> dict | None:
        with self.timed_session() as session:
            row = session.exec(
                select(WorkspaceMeta).where(
                    WorkspaceMeta.user_id == user_id,
                    WorkspaceMeta.workspace == workspace,
                )
            ).first()
        return _row_to_dict(row) if row else None

    def get_many(self, user_id: str, workspaces: list[str]) -> dict[str, dict]:
        if not workspaces:
            return {}
        with self.timed_session() as session:
            rows = session.exec(
                select(WorkspaceMeta).where(
                    WorkspaceMeta.user_id == user_id,
                    WorkspaceMeta.workspace.in_(workspaces),  # ty: ignore[unresolved-attribute]
                )
            ).all()
        return {r.workspace: _row_to_dict(r) for r in rows}

    def get_settings(self, user_id: str, workspace: str) -> str | None:
        """Raw settings JSON blob (None when unset). Parsing happens in the service."""
        with self.timed_session() as session:
            row = session.exec(
                select(WorkspaceMeta).where(
                    WorkspaceMeta.user_id == user_id,
                    WorkspaceMeta.workspace == workspace,
                )
            ).first()
        return row.settings if row else None

    def get_settings_many(self, user_id: str, workspaces: list[str]) -> dict[str, str | None]:
        """Raw settings blobs keyed by workspace; missing metadata rows are omitted."""
        if not workspaces:
            return {}
        with self.timed_session() as session:
            rows = session.exec(
                select(WorkspaceMeta).where(
                    WorkspaceMeta.user_id == user_id,
                    WorkspaceMeta.workspace.in_(workspaces),  # ty: ignore[unresolved-attribute]
                )
            ).all()
        return {row.workspace: row.settings for row in rows}

    def delete(self, user_id: str, workspace: str) -> None:
        with self.operation("delete", user_id=user_id, workspace=workspace) as operation:
            session = operation.session
            session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
                delete(WorkspaceMeta).where(
                    col(WorkspaceMeta.user_id) == user_id,
                    col(WorkspaceMeta.workspace) == workspace,
                )
            )
            session.commit()

    def set_settings(self, user_id: str, workspace: str, settings_json: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.operation("set_settings", user_id=user_id, workspace=workspace) as operation:
            session = operation.session
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "INSERT INTO workspace_meta"
                    " (user_id, workspace, description, folder, tags, settings, updated_at)"
                    " VALUES (:u, :w, '', '', NULL, :s, :now)"
                    " ON CONFLICT(user_id, workspace) DO UPDATE SET"
                    "   settings   = :s,"
                    "   updated_at = :now"
                ),
                {"u": user_id, "w": workspace, "s": settings_json, "now": now},
            )
            session.commit()
