# `builtins.list` is used in annotations because the public `list()` method
# below shadows the `list` builtin within the class body.
# TODO(Task 6): remove after list() → list_notes() rename
import builtins
import json
import re

from sqlalchemy import Engine, func, text
from sqlmodel import Session, col, select

from kajet_turbo.models import Note

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

    def list_under_folder(self, workspace: str, owner_id: str, prefix: str) -> builtins.list[Note]:
        """All notes whose folder is ``prefix`` or a descendant of it."""
        with Session(self._engine) as session:
            q = select(Note).where(
                Note.workspace == workspace,
                Note.owner_id == owner_id,
                (col(Note.folder) == prefix)
                | col(Note.folder).startswith(prefix + "/", autoescape=True),
            )
            return list(session.exec(q).all())

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

    def list(
        self,
        workspace: str,
        owner_id: str,
        tags: builtins.list[str] | None = None,
        limit: int | None = 20,
        folder: str | None = None,
        include_descendants: bool = True,
        _tag_repo=None,
    ) -> builtins.list[dict]:
        allowed: builtins.set[str] | None = None
        if tags:
            if _tag_repo is None:
                raise ValueError(
                    "list() requires _tag_repo when tags are specified; "
                    "pass a NoteTagRepository instance"
                )
            allowed = _tag_repo.note_ids_for_tags(
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

    def delete_for_workspace(self, workspace: str, owner_id: str, session: Session) -> None:
        """Delete note rows for (workspace, owner_id). Uses the caller's session; does not
        commit. FK constraint requires chunks to be deleted first (done by NoteChunkRepository
        in the same session before this method is called)."""
        session.execute(  # ty: ignore[deprecated] - raw SQL
            text("DELETE FROM notes WHERE workspace = :workspace AND owner_id = :owner_id"),
            {"workspace": workspace, "owner_id": owner_id},
        )
