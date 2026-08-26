from datetime import UTC, datetime

from nanoid import generate
from sqlalchemy import delete
from sqlmodel import Session, col, select

from kajet_turbo.models import DanglingLink
from kajet_turbo.repositories import DbRepository


class DanglingLinkRepository(DbRepository):
    """Stores unresolved wikilinks for validation-off workspaces, keyed by source note.
    Mirrors NoteRepository.replace_links: a source's rows are replaced wholesale on save."""

    repository_name = "dangling_links"

    def replace_for_source(
        self,
        source_note_id: str,
        workspace: str,
        owner_id: str,
        pairs: list[tuple[str, str]],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.operation(
            "replace_for_source",
            source_note_id=source_note_id,
            workspace=workspace,
            owner_id=owner_id,
        ) as operation:
            operation.add_fields(count=len(pairs))
            session = operation.session
            self.replace_for_source_in_session(
                session,
                source_note_id,
                workspace,
                owner_id,
                pairs,
                now=now,
            )
            session.commit()

    @staticmethod
    def replace_for_source_in_session(
        session: Session,
        source_note_id: str,
        workspace: str,
        owner_id: str,
        pairs: list[tuple[str, str]],
        *,
        now: str,
    ) -> None:
        session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
            delete(DanglingLink).where(col(DanglingLink.source_note_id) == source_note_id)
        )
        session.add_all(
            [
                DanglingLink(
                    id=generate(),
                    workspace=workspace,
                    owner_id=owner_id,
                    source_note_id=source_note_id,
                    target_folder=folder,
                    target_title=title,
                    created_at=now,
                )
                for folder, title in pairs
            ]
        )

    def exists(self, owner_id: str, workspace: str) -> bool:
        with self.timed_session() as session:
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
        with self.timed_session() as session:
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

    def source_ids_for_workspace(self, owner_id: str, workspace: str) -> set[str]:
        """Distinct sources used only to drain legacy workspace-wide heal jobs."""
        with self.timed_session() as session:
            rows = session.exec(
                select(DanglingLink.source_note_id)
                .where(
                    DanglingLink.owner_id == owner_id,
                    DanglingLink.workspace == workspace,
                )
                .distinct()
            ).all()
        return set(rows)

    def sources_for_titles(self, owner_id: str, workspace: str, titles: set[str]) -> set[str]:
        """Sources whose unresolved target title may change meaning after an identity edit."""
        if not titles:
            return set()
        with self.timed_session() as session:
            rows = session.exec(
                select(DanglingLink.source_note_id)
                .where(
                    DanglingLink.owner_id == owner_id,
                    DanglingLink.workspace == workspace,
                    col(DanglingLink.target_title).in_(titles),
                )
                .distinct()
            ).all()
        return set(rows)

    def delete(self, row_id: str) -> None:
        with self.operation("delete", row_id=row_id) as operation:
            session = operation.session
            session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
                delete(DanglingLink).where(col(DanglingLink.id) == row_id)
            )
            session.commit()

    def delete_for_source(self, source_note_id: str) -> None:
        with self.operation("delete_for_source", source_note_id=source_note_id) as operation:
            session = operation.session
            self.delete_for_source_in_session(session, source_note_id)
            session.commit()

    @staticmethod
    def delete_for_source_in_session(session: Session, source_note_id: str) -> None:
        session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
            delete(DanglingLink).where(col(DanglingLink.source_note_id) == source_note_id)
        )

    def delete_for_workspace(self, owner_id: str, workspace: str) -> None:
        with self.operation(
            "delete_for_workspace", owner_id=owner_id, workspace=workspace
        ) as operation:
            session = operation.session
            session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
                delete(DanglingLink).where(
                    col(DanglingLink.owner_id) == owner_id,
                    col(DanglingLink.workspace) == workspace,
                )
            )
            session.commit()
