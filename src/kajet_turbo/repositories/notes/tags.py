import builtins
import json
from datetime import UTC, datetime

from nanoid import generate
from sqlalchemy import CursorResult, Engine, delete
from sqlmodel import Session, col, select

from kajet_turbo.markdown import ancestors
from kajet_turbo.models import Note, NoteTag, Tag


class NoteTagRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

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

    def tag_counts(
        self,
        workspace: str,
        owner_id: str,
        folder: str | None = None,
        include_subfolders: bool = True,
    ) -> builtins.list[dict]:
        """Tags with ``count`` (distinct notes carrying exactly that tag), newest-popular
        first. Optionally scoped to a folder: ``include_subfolders`` toggles between the
        folder's subtree (prefix match, like :meth:`list_under_folder`) and that folder
        exactly. Tags with no notes in scope are omitted. One join, aggregated in Python."""
        with Session(self._engine) as session:
            q = (
                select(Tag.path, NoteTag.note_id)
                .join(NoteTag, col(NoteTag.tag_id) == col(Tag.id))
                .where(Tag.workspace == workspace, Tag.owner_id == owner_id)
            )
            if folder is not None:
                q = q.join(Note, col(Note.id) == col(NoteTag.note_id))
                if include_subfolders:
                    q = q.where(
                        (col(Note.folder) == folder)
                        | col(Note.folder).startswith(folder + "/", autoescape=True)
                    )
                else:
                    q = q.where(Note.folder == folder)
            pairs = session.exec(q).all()
        counts: dict[str, set[str]] = {}
        for tag_path, note_id in pairs:
            counts.setdefault(tag_path, set()).add(note_id)
        result = [
            {"path": p, "name": p.rsplit("/", 1)[-1], "count": len(ids)}
            for p, ids in counts.items()
        ]
        result.sort(key=lambda r: (-r["count"], r["path"]))
        return result
