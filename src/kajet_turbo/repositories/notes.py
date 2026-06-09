import json

from sqlalchemy import Engine, text
from sqlmodel import Session, select

from kajet_turbo.models import Note


class NoteRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def insert(
        self,
        note_id: str,
        workspace: str,
        owner_id: str,
        title: str,
        tags: list[str],
        created_at: str,
        updated_at: str,
        content: str,
    ) -> None:
        with Session(self._engine) as session:
            result = session.execute(
                text(
                    "INSERT INTO notes_fts (note_id, workspace, title, content)"
                    " VALUES (:note_id, :workspace, :title, :content)"
                ),
                {"note_id": note_id, "workspace": workspace, "title": title, "content": content},
            )
            fts_rowid = result.lastrowid
            note = Note(
                id=note_id,
                workspace=workspace,
                owner_id=owner_id,
                title=title,
                tags=json.dumps(tags),
                created_at=created_at,
                updated_at=updated_at,
                fts_rowid=fts_rowid,
            )
            session.add(note)
            session.commit()

    def get(self, note_id: str, owner_id: str | None = None) -> Note | None:
        with Session(self._engine) as session:
            q = select(Note).where(Note.id == note_id)
            if owner_id is not None:
                q = q.where(Note.owner_id == owner_id)
            return session.exec(q).first()

    def update(
        self,
        note_id: str,
        owner_id: str | None = None,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        updated_at: str = "",
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

            if title is not None or content is not None:
                old_fts = session.execute(
                    text("SELECT content FROM notes_fts WHERE rowid = :rowid"),
                    {"rowid": fts_rowid},
                ).fetchone()
                old_content = old_fts.content if old_fts else ""
                new_content = content if content is not None else old_content

                session.execute(
                    text("DELETE FROM notes_fts WHERE rowid = :rowid"), {"rowid": fts_rowid}
                )
                result = session.execute(
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
                session.execute(
                    text("DELETE FROM notes_fts WHERE rowid = :rowid"),
                    {"rowid": note.fts_rowid},
                )
            if note:
                session.delete(note)
            session.commit()

    def list(
        self,
        workspace: str,
        owner_id: str,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(Note)
                .where(Note.workspace == workspace, Note.owner_id == owner_id)
                .order_by(Note.updated_at.desc())
            ).all()

        result = []
        for note in rows:
            note_tags = json.loads(note.tags or "[]")
            if tags and not any(t in note_tags for t in tags):
                continue
            result.append({
                "note_id": note.id,
                "workspace": note.workspace,
                "owner_id": note.owner_id,
                "title": note.title,
                "tags": note_tags,
                "created_at": note.created_at,
                "updated_at": note.updated_at,
            })
            if len(result) >= limit:
                break
        return result

    def search_fts(self, query: str, workspace: str, owner_id: str, limit: int = 50) -> list[dict]:
        try:
            with Session(self._engine) as session:
                rows = session.execute(
                    text(
                        "SELECT n.id AS note_id, n.workspace, n.owner_id, n.title, n.tags, n.created_at, n.updated_at"
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
            session.execute(
                text(
                    "DELETE FROM notes_fts WHERE rowid IN"
                    " (SELECT fts_rowid FROM notes WHERE workspace = :workspace"
                    "  AND owner_id = :owner_id AND fts_rowid IS NOT NULL)"
                ),
                {"workspace": workspace, "owner_id": owner_id},
            )
            session.execute(
                text("DELETE FROM notes WHERE workspace = :workspace AND owner_id = :owner_id"),
                {"workspace": workspace, "owner_id": owner_id},
            )
            session.commit()

    def insert_vec(self, note_id: str, note_rowid: int, workspace: str, embedding: bytes) -> None:
        with Session(self._engine) as session:
            session.execute(
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

    def search_vec(self, embedding: bytes, workspace: str, owner_id: str, k: int = 20) -> list[dict]:
        with Session(self._engine) as session:
            rows = session.execute(
                text(
                    "SELECT n.id AS note_id, n.workspace, n.owner_id, n.title, n.tags, n.created_at, n.updated_at, v.distance"
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
    ) -> list[dict]:
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
            count = session.execute(
                text("SELECT COUNT(*) FROM notes_vec WHERE workspace = :workspace"),
                {"workspace": workspace},
            ).scalar()
        return (count or 0) > 0
