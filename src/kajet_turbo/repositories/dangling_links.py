from datetime import UTC, datetime

from nanoid import generate
from sqlalchemy import Engine, delete
from sqlmodel import Session, col, select

from kajet_turbo.models import DanglingLink


class DanglingLinkRepository:
    """Stores unresolved wikilinks for validation-off workspaces, keyed by source note.
    Mirrors NoteRepository.replace_links: a source's rows are replaced wholesale on save."""

    def __init__(self, engine: Engine):
        self._engine = engine

    def replace_for_source(
        self,
        source_note_id: str,
        workspace: str,
        owner_id: str,
        pairs: list[tuple[str, str]],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
                delete(DanglingLink).where(col(DanglingLink.source_note_id) == source_note_id)
            )
            for folder, title in pairs:
                session.add(
                    DanglingLink(
                        id=generate(),
                        workspace=workspace,
                        owner_id=owner_id,
                        source_note_id=source_note_id,
                        target_folder=folder,
                        target_title=title,
                        created_at=now,
                    )
                )
            session.commit()

    def exists(self, owner_id: str, workspace: str) -> bool:
        with Session(self._engine) as session:
            row = session.exec(
                select(DanglingLink.id)
                .where(
                    DanglingLink.owner_id == owner_id,
                    DanglingLink.workspace == workspace,
                )
                .limit(1)
            ).first()
        return row is not None

    def list_for_workspace(self, owner_id: str, workspace: str) -> list[dict]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(DanglingLink).where(
                    DanglingLink.owner_id == owner_id,
                    DanglingLink.workspace == workspace,
                )
            ).all()
        return [
            {
                "id": r.id,
                "source_note_id": r.source_note_id,
                "target_folder": r.target_folder,
                "target_title": r.target_title,
            }
            for r in rows
        ]

    def delete(self, row_id: str) -> None:
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
                delete(DanglingLink).where(col(DanglingLink.id) == row_id)
            )
            session.commit()

    def delete_for_source(self, source_note_id: str) -> None:
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
                delete(DanglingLink).where(col(DanglingLink.source_note_id) == source_note_id)
            )
            session.commit()
