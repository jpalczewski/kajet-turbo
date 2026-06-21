# `builtins.list` is used in annotations because the public `list()` method
# below shadows the `list` builtin within the class body.
import builtins
import json
import re
from datetime import UTC, datetime

from nanoid import generate
from sqlalchemy import CursorResult, Engine, delete, text
from sqlmodel import Session, col, func, select

from kajet_turbo.models import Note, NoteLink, NoteTag, Tag
from kajet_turbo.tags import ancestors

_NUM_SPLIT = re.compile(r"(\d+)")


def _folder_sort_key(note: Note) -> tuple:
    """README pinned first, then natural order by title (01, 02, … 10)."""
    is_readme = 0 if note.title.strip().lower() == "readme" else 1
    natural = [
        int(part) if part.isdigit() else part.lower() for part in _NUM_SPLIT.split(note.title)
    ]
    return (is_readme, natural)


class NoteRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def ensure_vec_table(self, dim: int) -> None:
        """Lazily create the dim-sharded vec0 table for this dimension. ``dim`` MUST be a
        positive int — it is interpolated into DDL, so a non-int is rejected to keep the
        statement injection-proof."""
        if not isinstance(dim, int) or isinstance(dim, bool) or dim <= 0:
            raise ValueError(f"dim must be a positive int, got {dim!r}")
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS note_chunks_vec_{dim} USING vec0("
                    " chunk_rowid INTEGER PRIMARY KEY,"
                    f" embedding float[{dim}],"
                    " workspace TEXT partition key,"
                    " owner_id TEXT,"
                    " note_id TEXT,"
                    " chunk_id TEXT"
                    ")"
                )
            )
            session.commit()

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
            note = Note(
                id=note_id,
                workspace=workspace,
                owner_id=owner_id,
                title=title,
                folder=folder,
                tags=json.dumps(tags),
                created_at=created_at,
                updated_at=updated_at,
            )
            session.add(note)
            session.commit()

    def replace_chunks(
        self,
        note_id: str,
        workspace: str,
        owner_id: str,
        title: str,
        chunks: builtins.list,  # list[kajet_turbo.chunking.Chunk]
        embeddings: builtins.list[builtins.list[float]] | None,
        dim: int | None,
    ) -> None:
        """Replace all chunks (and vectors) for a note. ``embeddings`` is None (chunks only
        → stale) or one vector per chunk (→ indexed, vectors into note_chunks_vec_{dim})."""
        from kajet_turbo.embedding.cache import pack_vector

        if embeddings is not None:
            if dim is None:
                raise ValueError("dim is required when embeddings are provided")
            if len(embeddings) != len(chunks):
                raise ValueError(
                    f"embeddings ({len(embeddings)}) must match chunks ({len(chunks)})"
                )
        now = datetime.now(UTC).isoformat()
        with Session(self._engine) as session:
            old = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "SELECT DISTINCT dim FROM note_chunks WHERE note_id = :nid AND dim IS NOT NULL"
                ),
                {"nid": note_id},
            ).fetchall()
            for (old_dim,) in old:
                session.execute(  # ty: ignore[deprecated] - raw SQL
                    text(f"DELETE FROM note_chunks_vec_{int(old_dim)} WHERE note_id = :nid"),
                    {"nid": note_id},
                )
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text("DELETE FROM note_chunks WHERE note_id = :nid"), {"nid": note_id}
            )
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text("DELETE FROM notes_fts WHERE note_id = :nid"), {"nid": note_id}
            )

            for i, chunk in enumerate(chunks):
                chunk_id = generate(size=12)
                result = session.execute(  # ty: ignore[deprecated] - raw SQL
                    text(
                        "INSERT INTO note_chunks"
                        " (id, note_id, workspace, owner_id, ordinal, header_path, content,"
                        "  char_start, char_end, dim, created_at)"
                        " VALUES (:id, :nid, :ws, :owner, :ord, :hp, :content,"
                        "  :cs, :ce, :dim, :now)"
                    ),
                    {
                        "id": chunk_id,
                        "nid": note_id,
                        "ws": workspace,
                        "owner": owner_id,
                        "ord": chunk.ordinal,
                        "hp": json.dumps(chunk.header_path),
                        "content": chunk.content,
                        "cs": chunk.char_start,
                        "ce": chunk.char_end,
                        "dim": dim if embeddings is not None else None,
                        "now": now,
                    },
                )
                assert isinstance(result, CursorResult)
                if embeddings is not None:
                    assert dim is not None  # validated above; narrows for the table name
                    session.execute(  # ty: ignore[deprecated] - raw SQL
                        text(
                            f"INSERT INTO note_chunks_vec_{int(dim)}"
                            " (chunk_rowid, embedding, workspace, owner_id, note_id, chunk_id)"
                            " VALUES (:rowid, :emb, :ws, :owner, :nid, :cid)"
                        ),
                        {
                            "rowid": result.lastrowid,
                            "emb": pack_vector(embeddings[i]),
                            "ws": workspace,
                            "owner": owner_id,
                            "nid": note_id,
                            "cid": chunk_id,
                        },
                    )
                session.execute(  # ty: ignore[deprecated] - raw SQL
                    text(
                        "INSERT INTO notes_fts"
                        " (chunk_id, note_id, workspace, title, header_path, content)"
                        " VALUES (:cid, :nid, :ws, :title, :hp, :content)"
                    ),
                    {
                        "cid": chunk_id,
                        "nid": note_id,
                        "ws": workspace,
                        "title": title,
                        "hp": " ".join(chunk.header_path),
                        "content": chunk.content,
                    },
                )

            state = "indexed" if embeddings is not None else "stale"
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text("UPDATE notes SET index_state = :s, indexed_at = :at WHERE id = :nid"),
                {
                    "s": state,
                    "at": now if embeddings is not None else None,
                    "nid": note_id,
                },
            )
            session.commit()

    def get_chunks(self, note_id: str) -> builtins.list[dict]:
        with Session(self._engine) as session:
            rows = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "SELECT id, ordinal, header_path, content, char_start, char_end, dim"
                    " FROM note_chunks WHERE note_id = :nid ORDER BY ordinal"
                ),
                {"nid": note_id},
            ).fetchall()
        return [dict(r._mapping) for r in rows]

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

            note.title = new_title
            note.tags = json.dumps(new_tags)
            note.updated_at = updated_at
            if folder is not None:
                note.folder = folder

            session.add(note)
            session.commit()

    def delete(self, note_id: str, owner_id: str | None = None) -> None:
        with Session(self._engine) as session:
            q = select(Note).where(Note.id == note_id)
            if owner_id is not None:
                q = q.where(Note.owner_id == owner_id)
            note = session.exec(q).first()
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

    def _ensure_tag(
        self, session: Session, workspace: str, owner_id: str, path: str, now: str
    ) -> str:
        """Return the tag id for ``path``, creating its full ancestor chain as needed."""
        parent_id: str | None = None
        tag_id = ""
        for ancestor in ancestors(path):
            existing = session.exec(
                select(Tag).where(
                    Tag.workspace == workspace,
                    Tag.owner_id == owner_id,
                    Tag.path == ancestor,
                )
            ).first()
            if existing is not None:
                tag_id = existing.id
            else:
                tag_id = generate(size=10)
                session.add(
                    Tag(
                        id=tag_id,
                        workspace=workspace,
                        owner_id=owner_id,
                        path=ancestor,
                        name=ancestor.rsplit("/", 1)[-1],
                        parent_id=parent_id,
                        created_at=now,
                    )
                )
                # Flush so the parent row exists before a child references it via
                # parent_id (self-FK is checked immediately with foreign_keys=ON).
                session.flush()
            parent_id = tag_id
        return tag_id

    def _gc_tags(self, session: Session, workspace: str, owner_id: str) -> None:
        """Delete tag rows in the workspace with no note_tags and no children.

        Loops bottom-up: removing a leaf may orphan its parent, so repeat until
        a pass deletes nothing. Workspaces are small, so the loop is cheap.
        """
        while True:
            referenced = select(NoteTag.tag_id)
            parents = select(Tag.parent_id).where(col(Tag.parent_id).is_not(None))
            result = session.execute(  # ty: ignore[deprecated] - DELETE statement
                delete(Tag).where(
                    col(Tag.workspace) == workspace,
                    col(Tag.owner_id) == owner_id,
                    col(Tag.id).not_in(referenced),
                    col(Tag.id).not_in(parents),
                )
            )
            assert isinstance(result, CursorResult)
            if result.rowcount == 0:
                break

    def sync_note_tags(
        self,
        note_id: str,
        workspace: str,
        owner_id: str,
        tagged: builtins.list[tuple[str, str]],
    ) -> None:
        """Rebuild ``note_tags`` for one note from ``tagged`` (``[(path, source)]``),
        materializing ancestor tag rows and garbage-collecting orphaned tags.
        ``tagged`` must already be normalized and deduped (frontmatter precedence).
        """
        now = datetime.now(UTC).isoformat()
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - DELETE statement
                delete(NoteTag).where(col(NoteTag.note_id) == note_id)
            )
            for path, source in tagged:
                tag_id = self._ensure_tag(session, workspace, owner_id, path, now)
                session.add(NoteTag(note_id=note_id, tag_id=tag_id, source=source))
            self._gc_tags(session, workspace, owner_id)
            session.commit()

    def delete_note_tags(self, note_id: str, workspace: str, owner_id: str) -> None:
        """Remove a note's tag links and GC any tags left empty (used on note delete)."""
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - DELETE statement
                delete(NoteTag).where(col(NoteTag.note_id) == note_id)
            )
            self._gc_tags(session, workspace, owner_id)
            session.commit()

    def delete_workspace_tags(self, workspace: str, owner_id: str) -> None:
        """Drop all tags + note_tags for a workspace (used before reindex)."""
        with Session(self._engine) as session:
            tag_ids = select(Tag.id).where(Tag.workspace == workspace, Tag.owner_id == owner_id)
            session.execute(  # ty: ignore[deprecated] - DELETE statement
                delete(NoteTag).where(col(NoteTag.tag_id).in_(tag_ids))
            )
            session.execute(  # ty: ignore[deprecated] - DELETE statement
                delete(Tag).where(col(Tag.workspace) == workspace, col(Tag.owner_id) == owner_id)
            )
            session.commit()

    def _descendant_tag_ids(
        self,
        session: Session,
        workspace: str,
        owner_id: str,
        path: str,
        include_descendants: bool,
    ) -> builtins.list[str]:
        """Tag ids for ``path`` (and, if requested, its subtree). Uses GLOB so ``_``
        in a path is treated literally (LIKE would treat it as a wildcard)."""
        q = select(Tag.id).where(Tag.workspace == workspace, Tag.owner_id == owner_id)
        if include_descendants:
            q = q.where((col(Tag.path) == path) | col(Tag.path).op("GLOB")(f"{path}/*"))
        else:
            q = q.where(Tag.path == path)
        return list(session.exec(q).all())

    def note_ids_for_tags(
        self,
        workspace: str,
        owner_id: str,
        paths: builtins.list[str],
        include_descendants: bool = True,
    ) -> builtins.set[str]:
        """Union of note ids matching any of ``paths`` (prefix-aware when requested)."""
        with Session(self._engine) as session:
            tag_ids: builtins.set[str] = set()
            for path in paths:
                tag_ids.update(
                    self._descendant_tag_ids(
                        session, workspace, owner_id, path, include_descendants
                    )
                )
            if not tag_ids:
                return set()
            rows = session.exec(
                select(NoteTag.note_id).where(col(NoteTag.tag_id).in_(tag_ids)).distinct()
            ).all()
            return set(rows)

    def notes_by_tag(
        self,
        workspace: str,
        owner_id: str,
        path: str,
        include_descendants: bool,
        limit: int | None,
    ) -> builtins.list[dict]:
        """Notes carrying ``path`` (or its subtree), newest first."""
        with Session(self._engine) as session:
            tag_ids = self._descendant_tag_ids(
                session, workspace, owner_id, path, include_descendants
            )
            if not tag_ids:
                return []
            note_ids = select(NoteTag.note_id).where(col(NoteTag.tag_id).in_(tag_ids)).distinct()
            q = (
                select(Note)
                .where(
                    Note.workspace == workspace,
                    Note.owner_id == owner_id,
                    col(Note.id).in_(note_ids),
                )
                .order_by(col(Note.updated_at).desc())
            )
            if limit is not None:
                q = q.limit(limit)
            rows = session.exec(q).all()
        return [
            {
                "note_id": n.id,
                "workspace": n.workspace,
                "owner_id": n.owner_id,
                "title": n.title,
                "folder": n.folder,
                "tags": json.loads(n.tags or "[]"),
                "created_at": n.created_at,
                "updated_at": n.updated_at,
            }
            for n in rows
        ]

    def tag_tree(self, workspace: str, owner_id: str) -> builtins.list[dict]:
        """All tags with ``exact_count`` (direct links) and ``descendant_count``
        (distinct notes on the node or any descendant). Computed in Python from one
        ``(path, note_id)`` join — workspaces are small."""
        with Session(self._engine) as session:
            paths = list(
                session.exec(
                    select(Tag.path).where(Tag.workspace == workspace, Tag.owner_id == owner_id)
                ).all()
            )
            pairs = session.exec(
                select(Tag.path, NoteTag.note_id)
                .join(NoteTag, col(NoteTag.tag_id) == col(Tag.id))
                .where(Tag.workspace == workspace, Tag.owner_id == owner_id)
            ).all()
        exact: dict[str, set[str]] = {p: set() for p in paths}
        for tag_path, note_id in pairs:
            exact[tag_path].add(note_id)
        result = []
        for p in sorted(paths):
            descendants: set[str] = set()
            for tag_path, ids in exact.items():
                if tag_path == p or tag_path.startswith(p + "/"):
                    descendants |= ids
            result.append(
                {
                    "path": p,
                    "name": p.rsplit("/", 1)[-1],
                    "exact_count": len(exact[p]),
                    "descendant_count": len(descendants),
                }
            )
        return result

    def list(
        self,
        workspace: str,
        owner_id: str,
        tags: builtins.list[str] | None = None,
        limit: int | None = 20,
        folder: str | None = None,
        include_descendants: bool = True,
    ) -> builtins.list[dict]:
        allowed: builtins.set[str] | None = None
        if tags:
            allowed = self.note_ids_for_tags(
                workspace, owner_id, tags, include_descendants=include_descendants
            )
            if not allowed:
                return []
        with Session(self._engine) as session:
            q = select(Note).where(Note.workspace == workspace, Note.owner_id == owner_id)
            if folder is not None:
                q = q.where(Note.folder == folder)
            rows = session.exec(q.order_by(col(Note.updated_at).desc())).all()

        # Folder browsing gets README-first + natural order; the global listing
        # (folder is None, e.g. MCP "recent notes") keeps recency order.
        if folder is not None:
            rows = sorted(rows, key=_folder_sort_key)

        result = []
        for note in rows:
            if allowed is not None and note.id not in allowed:
                continue
            result.append(
                {
                    "note_id": note.id,
                    "workspace": note.workspace,
                    "owner_id": note.owner_id,
                    "title": note.title,
                    "folder": note.folder,
                    "tags": json.loads(note.tags or "[]"),
                    "created_at": note.created_at,
                    "updated_at": note.updated_at,
                }
            )
            if limit is not None and len(result) >= limit:
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
                text("DELETE FROM notes_fts WHERE workspace = :workspace"),
                {"workspace": workspace},
            )
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text("DELETE FROM notes WHERE workspace = :workspace AND owner_id = :owner_id"),
                {"workspace": workspace, "owner_id": owner_id},
            )
            session.commit()

    def hybrid_search(
        self,
        query: str,
        workspace: str,
        owner_id: str,
        limit: int = 10,
    ) -> builtins.list[dict]:
        # Note-level FTS only. Chunk-level vector fusion arrives in Plan 4.
        return self.search_fts(query, workspace, owner_id, limit=50)[:limit]

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

    def get_index_meta(self, owner_id: str) -> dict | None:
        with Session(self._engine) as session:
            row = session.execute(  # ty: ignore[deprecated] - raw SQL
                text("SELECT backend, model, dim FROM index_meta WHERE owner_id = :o"),
                {"o": owner_id},
            ).fetchone()
        return dict(row._mapping) if row else None

    def upsert_index_meta(self, owner_id: str, backend: str, model: str, dim: int) -> None:
        now = datetime.now(UTC).isoformat()
        with Session(self._engine) as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "INSERT INTO index_meta (owner_id, backend, model, dim, updated_at)"
                    " VALUES (:o, :b, :m, :d, :now)"
                    " ON CONFLICT (owner_id) DO UPDATE SET"
                    "  backend = :b, model = :m, dim = :d, updated_at = :now"
                ),
                {"o": owner_id, "b": backend, "m": model, "d": dim, "now": now},
            )
            session.commit()
