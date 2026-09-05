from sqlalchemy import delete, text
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
        session.exec(delete(NoteLink).where(col(NoteLink.source_note_id) == source_note_id))
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
            session.exec(
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
        session.exec(delete(NoteLink).where(col(NoteLink.source_note_id) == source_note_id))

    @staticmethod
    def delete_links_to_in_session(session: Session, target_note_id: str) -> None:
        session.exec(delete(NoteLink).where(col(NoteLink.target_note_id) == target_note_id))

    @staticmethod
    def delete_workspace_links_in_session(session: Session, workspace: str, owner_id: str) -> None:
        session.exec(
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

    def neighborhood(
        self,
        note_id: str,
        workspace: str,
        owner_id: str,
        depth: int,
        *,
        include_cross_workspace: bool,
    ) -> list[tuple[str, str]]:
        """Return the directed, induced edge set in a note's undirected N-hop neighborhood.

        The recursive walk follows both link directions, while the final rows retain each
        wikilink's original direction.  Keeping the traversal in SQLite avoids one query per
        hop (and the frontend fan-out that would otherwise cause).
        """
        workspace_filter = ""
        if not include_cross_workspace:
            workspace_filter = """
                AND source.workspace = :workspace
                AND target.workspace = :workspace
            """
        query = text(
            f"""
            WITH RECURSIVE
            eligible_edges AS (
                SELECT links.source_note_id, links.target_note_id
                FROM note_links AS links
                JOIN notes AS source
                  ON source.id = links.source_note_id AND source.owner_id = :owner_id
                JOIN notes AS target
                  ON target.id = links.target_note_id AND target.owner_id = :owner_id
                WHERE links.owner_id = :owner_id
                {workspace_filter}
            ),
            adjacency AS (
                SELECT source_note_id AS source_id, target_note_id AS target_id
                FROM eligible_edges
                UNION
                SELECT target_note_id AS source_id, source_note_id AS target_id
                FROM eligible_edges
            ),
            walked(note_id, distance) AS (
                SELECT :note_id, 0
                UNION
                SELECT adjacency.target_id, walked.distance + 1
                FROM adjacency
                JOIN walked ON adjacency.source_id = walked.note_id
                WHERE walked.distance < :depth
            ),
            neighborhood_nodes AS (
                SELECT DISTINCT note_id FROM walked
            )
            SELECT edges.source_note_id, edges.target_note_id
            FROM eligible_edges AS edges
            JOIN neighborhood_nodes AS source ON source.note_id = edges.source_note_id
            JOIN neighborhood_nodes AS target ON target.note_id = edges.target_note_id
            ORDER BY edges.source_note_id, edges.target_note_id
            """
        )
        with self.timed_session() as session:
            rows = self._raw_execute(
                session,
                query,
                {
                    "note_id": note_id,
                    "workspace": workspace,
                    "owner_id": owner_id,
                    "depth": depth,
                },
            ).all()
        return [(source, target) for source, target in rows]
