# `builtins.list` is used in annotations because the public `list()` method
# below shadows the `list` builtin within the class body.
import builtins
import json

from sqlalchemy import CursorResult, Engine, delete, text
from sqlmodel import Session, col, func, select

from kajet_turbo.models import Note, NoteLink


class NoteRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def insert(
        self,
        note_id: str,
        workspace: str,
        owner_id: str,
        title: str,
        tags: builtins.list[str],
        created_at: str,
        updated_at: str,
        content: str,
        folder: str = "",
    ) -> None:
        with Session(self._engine) as session:
            result = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "INSERT INTO notes_fts (note_id, workspace, title, content)"
                    " VALUES (:note_id, :workspace, :title, :content)"
                ),
                {"note_id": note_id, "workspace": workspace, "title": title, "content": content},
            )
            # SQLite DML always yields a CursorResult; needed for `lastrowid`.
            assert isinstance(result, CursorResult)
            fts_rowid = result.lastrowid
            note = Note(
                id=note_id,
                workspace=workspace,
                owner_id=owner_id,
                title=title,
                folder=folder,
                tags=json.dumps(tags),
                created_at=created_at,
                updated_at=updated_at,
                fts_rowid=fts_rowid,
            )
            session.add(note)
            session.commit()

    def check_unique(self, workspace: str, owner_id: str, folder: str, title: str) -> bool:
        """Returns True if no note with this (workspace, owner_id, folder, title) exists."""
        with Session(self._engine) as session:
            q = select(Note).where(
                Note.workspace == workspace,
                Note.owner_id == owner_id,
                Note.folder == folder,
                Note.title == title,
            )
            return session.exec(q).first() is None

    def get(self, note_id: str, owner_id: str | None = None) -> Note | None:
        with Session(self._engine) as session:
            q = select(Note).where(Note.id == note_id)
            if owner_id is not None:
                q = q.where(Note.owner_id == owner_id)
            return session.exec(q).first()

    def get_by_path(self, workspace: str, owner_id: str, folder: str, title: str) -> Note | None:
        """Resolve a note by its workspace-relative (folder, title) natural key."""
        with Session(self._engine) as session:
            q = select(Note).where(
                Note.workspace == workspace,
                Note.owner_id == owner_id,
                Note.folder == folder,
                Note.title == title,
            )
            return session.exec(q).first()

    def resolve_paths(
        self,
        workspace: str,
        owner_id: str,
        pairs: builtins.list[tuple[str, str]],
    ) -> dict[tuple[str, str], str]:
        """Map ``(folder, title) -> note_id`` for the pairs that exist.

        One query loads the workspace's ``(folder, title, id)`` index into memory (a single
        user's workspace is small), avoiding N+1 lookups during link validation.
        """
        if not pairs:
            return {}
        wanted = set(pairs)
        with Session(self._engine) as session:
            rows = session.exec(
                select(Note.folder, Note.title, Note.id).where(
                    Note.workspace == workspace, Note.owner_id == owner_id
                )
            ).all()
        index = {(folder, title): note_id for folder, title, note_id in rows}
        return {pair: index[pair] for pair in wanted if pair in index}

    def update(
        self,
        note_id: str,
        owner_id: str | None = None,
        title: str | None = None,
        content: str | None = None,
        tags: builtins.list[str] | None = None,
        updated_at: str = "",
        folder: str | None = None,
    ) -> None:
        with Session(self._engine) as session:
            q = select(Note).where(Note.id == note_id)
            if owner_id is not None:
                q = q.where(Note.owner_id == owner_id)
            note = session.exec(q).first()
            if note is None:
                raise ValueError(f"Note {note_id} not found")

            new_title = title if title is not None else note.title
            new_tags = tags if tags is not None else json.loads(note.tags or "[]")
            fts_rowid = note.fts_rowid
            workspace = note.workspace

            note.title = new_title
            note.tags = json.dumps(new_tags)
            note.updated_at = updated_at
            if folder is not None:
                note.folder = folder

            if title is not None or content is not None:
                old_fts = session.execute(  # ty: ignore[deprecated] - raw SQL
                    text("SELECT content FROM notes_fts WHERE rowid = :rowid"),
                    {"rowid": fts_rowid},
                ).fetchone()
                old_content = old_fts.content if old_fts else ""
                new_content = content if content is not None else old_content

                session.execute(  # ty: ignore[deprecated] - raw SQL
                    text("DELETE FROM notes_fts WHERE rowid = :rowid"), {"rowid": fts_rowid}
                )
                result = session.execute(  # ty: ignore[deprecated] - raw SQL
                    text(
                        "INSERT INTO notes_fts (note_id, workspace, title, content)"
                        " VALUES (:note_id, :workspace, :title, :content)"
                    ),
                    {
                        "note_id": note_id,
                        "workspace": workspace,
                        "title": new_title,
                        "content": new_content,
                    },
                )
                # SQLite DML always yields a CursorResult; needed for `lastrowid`.
                assert isinstance(result, CursorResult)
                note.fts_rowid = result.lastrowid

            session.add(note)
            session.commit()

    def delete(self, note_id: str, owner_id: str | None = None) -> None:
        with Session(self._engine) as session:
            q = select(Note).where(Note.id == note_id)
            if owner_id is not None:
                q = q.where(Note.owner_id == owner_id)
            note = session.exec(q).first()
            if note and note.fts_rowid:
                session.execute(  # ty: ignore[deprecated] - raw SQL
                    text("DELETE FROM notes_fts WHERE rowid = :rowid"),
                    {"rowid": note.fts_rowid},
                )
            if note:
                session.delete(note)
            session.commit()

    def replace_links(
        self,
        source_note_id: str,
        workspace: str,
        owner_id: str,
        target_ids: builtins.set[str],
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

    def backlinks(self, target_note_id: str) -> builtins.list[str]:
        """Return source note_ids that link to ``target_note_id`` (index-only scan)."""
        with Session(self._engine) as session:
            rows = session.exec(
                select(NoteLink.source_note_id).where(NoteLink.target_note_id == target_note_id)
            ).all()
        return list(rows)

    def outlinks(self, source_note_id: str) -> builtins.list[str]:
        """Return target note_ids that ``source_note_id`` links to (uses the composite PK)."""
        with Session(self._engine) as session:
            rows = session.exec(
                select(NoteLink.target_note_id).where(NoteLink.source_note_id == source_note_id)
            ).all()
        return list(rows)

    def list(
        self,
        workspace: str,
        owner_id: str,
        tags: builtins.list[str] | None = None,
        limit: int = 20,
        folder: str | None = None,
    ) -> builtins.list[dict]:
        with Session(self._engine) as session:
            q = select(Note).where(Note.workspace == workspace, Note.owner_id == owner_id)
            if folder is not None:
                q = q.where(Note.folder == folder)
            rows = session.exec(q.order_by(col(Note.updated_at).desc())).all()

        result = []
        for note in rows:
            note_tags = json.loads(note.tags or "[]")
            if tags and not any(t in note_tags for t in tags):
                continue
            result.append(
                {
                    "note_id": note.id,
                    "workspace": note.workspace,
                    "owner_id": note.owner_id,
                    "title": note.title,
                    "folder": note.folder,
                    "tags": note_tags,
                    "created_at": note.created_at,
                    "updated_at": note.updated_at,
                }
            )
            if len(result) >= limit:
                break
        return result

    def list_folders(self, workspace: str, owner_id: str) -> builtins.list[str]:
        with Session(self._engine) as session:
            rows = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "SELECT DISTINCT folder FROM notes"
                    " WHERE workspace = :workspace AND owner_id = :owner_id AND folder != ''"
                ),
                {"workspace": workspace, "owner_id": owner_id},
            ).fetchall()
        return [row[0] for row in rows]

    def search_fts(
        self, query: str, workspace: str, owner_id: str, limit: int = 50
    ) -> builtins.list[dict]:
        try:
            with Session(self._engine) as session:
                rows = session.execute(  # ty: ignore[deprecated] - raw SQL
                    text(
                        "SELECT n.id AS note_id, n.workspace, n.owner_id,"
                        " n.title, n.tags, n.created_at, n.updated_at"
                        " FROM notes_fts"
                        " JOIN notes n ON n.fts_rowid = notes_fts.rowid"
                        " WHERE notes_fts MATCH :query"
                        "  AND n.workspace = :workspace AND n.owner_id = :owner_id"
                        " ORDER BY rank LIMIT :limit"
                    ),
                    {"query": query, "workspace": workspace, "owner_id": owner_id, "limit": limit},
                ).fetchall()
        except Exception:
            return []
        return [{**dict(r._mapping), "tags": json.loads(r.tags or "[]")} for r in rows]

    def delete_workspace_notes(self, workspace: str, owner_id: str) -> None:
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "DELETE FROM notes_fts WHERE rowid IN"
                    " (SELECT fts_rowid FROM notes WHERE workspace = :workspace"
                    "  AND owner_id = :owner_id AND fts_rowid IS NOT NULL)"
                ),
                {"workspace": workspace, "owner_id": owner_id},
            )
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text("DELETE FROM notes WHERE workspace = :workspace AND owner_id = :owner_id"),
                {"workspace": workspace, "owner_id": owner_id},
            )
            session.commit()

    def insert_vec(self, note_id: str, note_rowid: int, workspace: str, embedding: bytes) -> None:
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "INSERT INTO notes_vec (note_rowid, embedding, workspace, note_id)"
                    " VALUES (:note_rowid, :embedding, :workspace, :note_id)"
                ),
                {
                    "note_rowid": note_rowid,
                    "embedding": embedding,
                    "workspace": workspace,
                    "note_id": note_id,
                },
            )
            session.commit()

    def search_vec(
        self, embedding: bytes, workspace: str, owner_id: str, k: int = 20
    ) -> builtins.list[dict]:
        with Session(self._engine) as session:
            rows = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "SELECT n.id AS note_id, n.workspace, n.owner_id,"
                    " n.title, n.tags, n.created_at, n.updated_at, v.distance"
                    " FROM notes_vec v"
                    " JOIN notes n ON n.id = v.note_id"
                    " WHERE v.embedding MATCH :embedding AND k = :k AND v.workspace = :workspace"
                    "  AND n.owner_id = :owner_id"
                    " ORDER BY v.distance"
                ),
                {"embedding": embedding, "k": k, "workspace": workspace, "owner_id": owner_id},
            ).fetchall()
        return [{**dict(r._mapping), "tags": json.loads(r.tags or "[]")} for r in rows]

    def hybrid_search(
        self,
        query: str,
        workspace: str,
        owner_id: str,
        embedding: bytes | None = None,
        limit: int = 10,
    ) -> builtins.list[dict]:
        fts_results = self.search_fts(query, workspace, owner_id, limit=50)
        if embedding is None:
            return fts_results[:limit]

        vec_results = self.search_vec(embedding, workspace, owner_id, k=50)
        scores: dict[str, float] = {}
        for rank, note in enumerate(fts_results):
            scores[note["note_id"]] = scores.get(note["note_id"], 0) + 1 / (60 + rank)
        for rank, note in enumerate(vec_results):
            scores[note["note_id"]] = scores.get(note["note_id"], 0) + 1 / (60 + rank)

        all_notes = {n["note_id"]: n for n in fts_results + vec_results}
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [all_notes[note_id] for note_id, _ in ranked if note_id in all_notes]

    def has_vec_index(self, workspace: str) -> bool:
        with Session(self._engine) as session:
            count = session.execute(  # ty: ignore[deprecated] - raw SQL
                text("SELECT COUNT(*) FROM notes_vec WHERE workspace = :workspace"),
                {"workspace": workspace},
            ).scalar()
        return (count or 0) > 0

    def workspace_stats(self, owner_id: str, workspaces: builtins.list[str]) -> dict[str, dict]:
        if not workspaces:
            return {}
        with Session(self._engine) as session:
            rows = session.exec(
                select(
                    Note.workspace,
                    func.count().label("file_count"),
                    func.max(Note.updated_at).label("last_updated"),
                )
                .where(Note.owner_id == owner_id, col(Note.workspace).in_(workspaces))
                .group_by(Note.workspace)
            )
            return {
                workspace: {"file_count": file_count, "last_updated": last_updated}
                for workspace, file_count, last_updated in rows
            }
