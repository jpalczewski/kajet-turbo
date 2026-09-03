from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, col, select

from kajet_turbo.models import NoteLink
from kajet_turbo.repositories import DbRepository


class NoteLinkRepository(DbRepository):
    repository_name = "note_links"

    @staticmethod
    def replace_links_in_session(
        session: Session,
        source_note_id: str,
        workspace: str,
        owner_id: str,
        target_ids: set[str],
    ) -> None:
        session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
            delete(NoteLink).where(col(NoteLink.source_note_id) == source_note_id)
        )
        session.add_all(
            [
                NoteLink(
                    source_note_id=source_note_id,
                    target_note_id=target_id,
                    workspace=workspace,
                    owner_id=owner_id,
                )
                for target_id in target_ids
            ]
        )

    def replace_links(
        self,
        source_note_id: str,
        workspace: str,
        owner_id: str,
        target_ids: set[str],
    ) -> None:
        """Replace the set of outgoing links for ``source_note_id`` (delete + reinsert)."""
        with self.operation(
            "replace_links",
            source_note_id=source_note_id,
            workspace=workspace,
            owner_id=owner_id,
            targets=len(target_ids),
        ) as operation:
            session = operation.session
            self.replace_links_in_session(session, source_note_id, workspace, owner_id, target_ids)
            session.commit()

    def add_link(
        self, source_note_id: str, target_note_id: str, workspace: str, owner_id: str
    ) -> None:
        """Insert one outgoing edge, idempotently (ON CONFLICT DO NOTHING on the composite
        PK). Unlike replace_links, leaves the source's other edges intact — useful for
        maintenance/backfill code that adds one known edge."""
        with self.operation(
            "add_link", source_note_id=source_note_id, target_note_id=target_note_id
        ) as operation:
            session = operation.session
            session.execute(  # ty: ignore[deprecated] — sqlite INSERT ON CONFLICT requires execute(), not exec()
                sqlite_insert(NoteLink)
                .values(
                    source_note_id=source_note_id,
                    target_note_id=target_note_id,
                    workspace=workspace,
                    owner_id=owner_id,
                )
                .on_conflict_do_nothing()
            )
            session.commit()

    def delete_links_from(self, source_note_id: str) -> None:
        with self.operation("delete_links_from", source_note_id=source_note_id) as operation:
            session = operation.session
            self.delete_links_from_in_session(session, source_note_id)
            session.commit()

    @staticmethod
    def delete_links_from_in_session(session: Session, source_note_id: str) -> None:
        session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
            delete(NoteLink).where(col(NoteLink.source_note_id) == source_note_id)
        )

    @staticmethod
    def delete_links_to_in_session(session: Session, target_note_id: str) -> None:
        session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
            delete(NoteLink).where(col(NoteLink.target_note_id) == target_note_id)
        )

    @staticmethod
    def delete_workspace_links_in_session(session: Session, workspace: str, owner_id: str) -> None:
        session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
            delete(NoteLink).where(
                col(NoteLink.workspace) == workspace,
                col(NoteLink.owner_id) == owner_id,
            )
        )

    def backlinks(self, target_note_id: str, same_workspace: str | None = None) -> list[str]:
        """Return source note_ids that link to ``target_note_id``.

        When ``same_workspace`` is given, only returns links whose source note is
        in that workspace (filters cross-workspace backlinks out).
        """
        with self.timed_session() as session:
            query = select(NoteLink.source_note_id).where(NoteLink.target_note_id == target_note_id)
            if same_workspace is not None:
                query = query.where(col(NoteLink.workspace) == same_workspace)
            rows = session.exec(query).all()
        return list(rows)

    def backlinks_many(
        self, target_note_ids: list[str], same_workspace: str | None = None
    ) -> set[str]:
        """Union of ``backlinks`` for several targets in one query (a folder move's worth)."""
        if not target_note_ids:
            return set()
        with self.timed_session() as session:
            query = select(NoteLink.source_note_id).where(
                col(NoteLink.target_note_id).in_(target_note_ids)
            )
            if same_workspace is not None:
                query = query.where(col(NoteLink.workspace) == same_workspace)
            rows = session.exec(query).all()
        return set(rows)

    def outlinks(self, source_note_id: str) -> list[str]:
        """Return target note_ids that ``source_note_id`` links to (uses the composite PK)."""
        with self.timed_session() as session:
            rows = session.exec(
                select(NoteLink.target_note_id).where(NoteLink.source_note_id == source_note_id)
            ).all()
        return list(rows)

    def list_for_workspace(self, workspace: str, owner_id: str) -> list[tuple[str, str]]:
        """Every (source_note_id, target_note_id) edge in the workspace, for a bulk graph view."""
        with self.timed_session() as session:
            rows = session.exec(
                select(NoteLink.source_note_id, NoteLink.target_note_id).where(
                    col(NoteLink.workspace) == workspace,
                    col(NoteLink.owner_id) == owner_id,
                )
            ).all()
        return [(s, t) for s, t in rows]
