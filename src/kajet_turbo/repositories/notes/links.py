from sqlalchemy import Engine, delete
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, col, select

from kajet_turbo.models import NoteLink


class NoteLinkRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def replace_links(
        self,
        source_note_id: str,
        workspace: str,
        owner_id: str,
        target_ids: set[str],
    ) -> None:
        """Replace the set of outgoing links for ``source_note_id`` (delete + reinsert)."""
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
                delete(NoteLink).where(col(NoteLink.source_note_id) == source_note_id)
            )
            for target_id in target_ids:
                session.add(
                    NoteLink(
                        source_note_id=source_note_id,
                        target_note_id=target_id,
                        workspace=workspace,
                        owner_id=owner_id,
                    )
                )
            session.commit()

    def add_link(
        self, source_note_id: str, target_note_id: str, workspace: str, owner_id: str
    ) -> None:
        """Insert one outgoing edge, idempotently (ON CONFLICT DO NOTHING on the composite
        PK). Unlike replace_links, leaves the source's other edges intact — used by the
        reverse-heal job to add a single newly-resolved edge."""
        with Session(self._engine) as session:
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
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
                delete(NoteLink).where(col(NoteLink.source_note_id) == source_note_id)
            )
            session.commit()

    def delete_links_to(self, target_note_id: str) -> None:
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
                delete(NoteLink).where(col(NoteLink.target_note_id) == target_note_id)
            )
            session.commit()

    def delete_workspace_links(self, workspace: str, owner_id: str) -> None:
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - exec() can't type a DELETE statement
                delete(NoteLink).where(
                    col(NoteLink.workspace) == workspace,
                    col(NoteLink.owner_id) == owner_id,
                )
            )
            session.commit()

    def backlinks(self, target_note_id: str) -> list[str]:
        """Return source note_ids that link to ``target_note_id`` (index-only scan)."""
        with Session(self._engine) as session:
            rows = session.exec(
                select(NoteLink.source_note_id).where(NoteLink.target_note_id == target_note_id)
            ).all()
        return list(rows)

    def outlinks(self, source_note_id: str) -> list[str]:
        """Return target note_ids that ``source_note_id`` links to (uses the composite PK)."""
        with Session(self._engine) as session:
            rows = session.exec(
                select(NoteLink.target_note_id).where(NoteLink.source_note_id == source_note_id)
            ).all()
        return list(rows)
