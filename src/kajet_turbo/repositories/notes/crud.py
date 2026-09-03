import json
import re
from itertools import batched
from typing import cast

from sqlalchemy import delete, func, text
from sqlmodel import Session, col, select

from kajet_turbo.markdown import IndexedNote
from kajet_turbo.models import Note, NoteTag, Tag
from kajet_turbo.perf import timed
from kajet_turbo.periods import parse_period_key
from kajet_turbo.repositories import DbRepository

_NUM_SPLIT = re.compile(r"(\d+)")
_UNSET = object()

# Conservative chunk size for an `id IN (...)` clause: SQLite's compiled bound-parameter
# limit was 999 before 3.32.0 and is 32766 by default since — this stays well under both
# without needing to detect the running version.
_IN_CLAUSE_CHUNK_SIZE = 900


def folder_sort_key(note: Note) -> tuple:
    """README pinned first, then natural order by title (01, 02, … 10)."""
    is_readme = 0 if note.title.strip().lower() == "readme" else 1
    natural = [
        int(part) if part.isdigit() else part.lower() for part in _NUM_SPLIT.split(note.title)
    ]
    return (is_readme, natural)


class NoteRepository(DbRepository):
    repository_name = "notes"

    def insert(
        self,
        note_id: str,
        workspace: str,
        owner_id: str,
        title: str,
        tags: list[str],
        created_at: str,
        updated_at: str,
        folder: str = "",
        occurred_at: str | None = None,
        period: str | None = None,
    ) -> None:
        with self.operation(
            "insert", note_id=note_id, workspace=workspace, owner_id=owner_id
        ) as operation:
            session = operation.session
            self.insert_in_session(
                session,
                Note(
                    id=note_id,
                    workspace=workspace,
                    owner_id=owner_id,
                    title=title,
                    folder=folder,
                    tags=json.dumps(tags),
                    created_at=created_at,
                    updated_at=updated_at,
                    occurred_at=occurred_at,
                    period=period,
                ),
            )
            session.commit()

    @staticmethod
    def insert_in_session(session: Session, note: Note) -> None:
        """Add one new note row in a caller-owned transaction; does not commit."""
        session.add(note)

    def check_unique(self, workspace: str, owner_id: str, folder: str, title: str) -> bool:
        """Returns True if no note with this (workspace, owner_id, folder, title) exists."""
        with self.timed_session() as session:
            q = select(Note).where(
                Note.workspace == workspace,
                Note.owner_id == owner_id,
                Note.folder == folder,
                Note.title == title,
            )
            return session.exec(q).first() is None

    def get(self, note_id: str, owner_id: str | None = None) -> Note | None:
        with self.timed_session() as session:
            q = select(Note).where(Note.id == note_id)
            if owner_id is not None:
                q = q.where(Note.owner_id == owner_id)
            return session.exec(q).first()

    def get_many(self, note_ids: list[str], owner_id: str) -> list[Note]:
        """Batch of ``get`` for many ids in one or more queries. Missing ids are simply
        absent from the result — callers already handle that shape from ``get``.

        Chunks the ``IN (...)`` clause at ``_IN_CLAUSE_CHUNK_SIZE`` ids per query — a
        caller like ``NoteLinkService.graph()`` can pass every note id in a workspace,
        which would otherwise risk SQLite's compiled bound-parameter limit on a large,
        long-lived workspace.
        """
        if not note_ids:
            return []
        with self.timed_session() as session:
            result: list[Note] = []
            for chunk in batched(note_ids, _IN_CLAUSE_CHUNK_SIZE, strict=False):
                q = select(Note).where(col(Note.id).in_(chunk), Note.owner_id == owner_id)
                result.extend(session.exec(q).all())
            return result

    def get_by_path(self, workspace: str, owner_id: str, folder: str, title: str) -> Note | None:
        """Resolve a note by its workspace-relative (folder, title) natural key."""
        with self.timed_session() as session:
            q = select(Note).where(
                Note.workspace == workspace,
                Note.owner_id == owner_id,
                Note.folder == folder,
                Note.title == title,
            )
            return session.exec(q).first()

    def list_under_folder(self, workspace: str, owner_id: str, prefix: str) -> list[Note]:
        """All notes whose folder is ``prefix`` or a descendant of it."""
        with self.timed_session() as session:
            q = select(Note).where(
                Note.workspace == workspace,
                Note.owner_id == owner_id,
                (col(Note.folder) == prefix)
                | col(Note.folder).startswith(prefix + "/", autoescape=True),
            )
            return list(session.exec(q).all())

    def note_ids_under_folder(self, workspace: str, owner_id: str, prefix: str) -> set[str]:
        """Note ids whose folder is ``prefix`` or a descendant of it (same predicate as
        list_under_folder, projected to ids only — used to narrow search)."""
        with self.timed_session() as session:
            rows = session.exec(
                select(Note.id).where(
                    Note.workspace == workspace,
                    Note.owner_id == owner_id,
                    (col(Note.folder) == prefix)
                    | col(Note.folder).startswith(prefix + "/", autoescape=True),
                )
            ).all()
        return set(rows)

    def count_stale(self, workspace: str, owner_id: str) -> int:
        """Count notes awaiting deferred embedding. Folded into the search cache key so
        a worker flipping notes stale→indexed (which bumps no epoch, possibly in another
        process) invalidates cached vector-less rankings on the next search."""
        with self.timed_session() as session:
            count = session.exec(
                select(func.count()).where(
                    Note.workspace == workspace,
                    Note.owner_id == owner_id,
                    Note.index_state == "stale",
                )
            ).one()
        return int(count)

    def list_paths(self, workspace: str, owner_id: str) -> list[IndexedNote]:
        """Every note's ``(id, folder, title)`` in the workspace — the raw material for a
        ``LinkIndex``. One narrow query per operation (a single user's workspace is small)
        instead of N+1 lookups during link resolution."""
        with self.timed_session() as session:
            rows = session.exec(
                select(Note.id, Note.folder, Note.title).where(
                    Note.workspace == workspace, Note.owner_id == owner_id
                )
            ).all()
        return [IndexedNote(note_id, folder, title) for note_id, folder, title in rows]

    def search_metadata(
        self, workspace: str, owner_id: str, query: str, limit: int = 20
    ) -> list[dict]:
        """Deterministic note-level matches on title / folder / tag paths — every token in
        ``query`` must be a casefold substring of the title, folder, or some tag path
        (SQLite LIKE/lower() are ASCII-only, wrong for Polish text, so matching is Python-
        side over two narrow SELECTs; workspaces are small enough for this to be cheap).
        Covers what FTS/vector search structurally cannot: tags are stripped from
        frontmatter before indexing, and folder paths are never indexed at all.
        Ranked exact-title match first, then title-prefix match, then updated_at desc.
        """
        tokens = [t for t in query.casefold().split() if t]
        if not tokens:
            return []
        query_cf = query.casefold()
        with timed("meta_ms"):
            with self.timed_session() as session:
                notes = session.exec(
                    select(Note.id, Note.title, Note.folder, Note.updated_at).where(
                        Note.workspace == workspace, Note.owner_id == owner_id
                    )
                ).all()
                tag_rows = session.exec(
                    select(NoteTag.note_id, Tag.path)
                    .join(Tag, col(NoteTag.tag_id) == col(Tag.id))
                    .where(Tag.workspace == workspace, Tag.owner_id == owner_id)
                ).all()

            tags_by_note: dict[str, list[str]] = {}
            for note_id, path in tag_rows:
                tags_by_note.setdefault(note_id, []).append(path.casefold())

            hits: list[dict] = []
            for note_id, title, folder, updated_at in notes:
                title_cf = title.casefold()
                folder_cf = folder.casefold()
                note_tags_cf = tags_by_note.get(note_id, [])
                matched_on: set[str] = set()
                for token in tokens:
                    token_hit = False
                    if token in title_cf:
                        matched_on.add("title")
                        token_hit = True
                    if token in folder_cf:
                        matched_on.add("folder")
                        token_hit = True
                    if any(token in t for t in note_tags_cf):
                        matched_on.add("tag")
                        token_hit = True
                    if not token_hit:
                        matched_on.clear()
                        break
                if not matched_on:
                    continue
                hits.append(
                    {
                        "note_id": note_id,
                        "title": title,
                        "folder": folder,
                        "updated_at": updated_at,
                        "matched_on": sorted(matched_on),
                        "_exact_title": title_cf == query_cf,
                        "_prefix_title": title_cf.startswith(query_cf),
                    }
                )

            # Stable multi-key sort: apply least-significant key first (Python sort is stable).
            hits.sort(key=lambda h: h["updated_at"], reverse=True)
            hits.sort(key=lambda h: h["_prefix_title"], reverse=True)
            hits.sort(key=lambda h: h["_exact_title"], reverse=True)
            for h in hits:
                del h["_exact_title"]
                del h["_prefix_title"]
            results = hits[:limit]
        self.log_operation(
            "metadata_search",
            workspace=workspace,
            query_tokens=len(tokens),
            matches=len(results),
        )
        return results

    def update(
        self,
        note_id: str,
        owner_id: str | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
        updated_at: str = "",
        folder: str | None = None,
        created_at: str | None = None,
        occurred_at: str | None | object = _UNSET,
        period: str | None | object = _UNSET,
        bump_index_generation: bool = False,
    ) -> None:
        with self.operation("update", note_id=note_id, owner_id=owner_id) as operation:
            session = operation.session
            self.update_in_session(
                session,
                note_id,
                owner_id=owner_id,
                title=title,
                tags=tags,
                updated_at=updated_at,
                folder=folder,
                created_at=created_at,
                occurred_at=occurred_at,
                period=period,
                bump_index_generation=bump_index_generation,
            )
            session.commit()

    @staticmethod
    def update_in_session(
        session: Session,
        note_id: str,
        owner_id: str | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
        updated_at: str = "",
        folder: str | None = None,
        created_at: str | None = None,
        occurred_at: str | None | object = _UNSET,
        period: str | None | object = _UNSET,
        bump_index_generation: bool = False,
    ) -> None:
        """Update one note row in a caller-owned transaction; does not commit."""
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
        if created_at is not None:
            note.created_at = created_at
        if occurred_at is not _UNSET:
            note.occurred_at = cast("str | None", occurred_at)
        if period is not _UNSET:
            note.period = cast("str | None", period)
        if bump_index_generation:
            note.index_generation += 1

        session.add(note)

    def delete(self, note_id: str, owner_id: str | None = None) -> None:
        with self.operation("delete", note_id=note_id, owner_id=owner_id) as operation:
            session = operation.session
            count = self.delete_in_session(session, note_id, owner_id)
            if not count:
                operation.suppress_log()
            session.commit()

    @staticmethod
    def delete_in_session(session: Session, note_id: str, owner_id: str | None = None) -> int:
        """Delete one note in a caller-owned transaction and return affected rows."""
        stmt = delete(Note).where(col(Note.id) == note_id)
        if owner_id is not None:
            stmt = stmt.where(col(Note.owner_id) == owner_id)
        result = session.execute(stmt)  # ty: ignore[deprecated] - DELETE statement
        return result.rowcount  # ty: ignore[unresolved-attribute] - Result has rowcount at runtime

    def list_notes(
        self,
        workspace: str,
        owner_id: str,
        tags: list[str] | None = None,
        limit: int | None = 20,
        folder: str | None = None,
        include_descendants: bool = True,
        sort: str = "default",
        _tag_repo=None,
    ) -> list[dict]:
        allowed: set[str] | None = None
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
        with self.timed_session() as session:
            q = select(Note).where(Note.workspace == workspace, Note.owner_id == owner_id)
            if folder is not None:
                q = q.where(Note.folder == folder)
            order_col = col(Note.created_at) if sort == "created" else col(Note.updated_at)
            rows = session.exec(q.order_by(order_col.desc())).all()

        # Folder browsing gets README-first + natural order by default; sort='title'
        # forces that ordering globally too; sort='updated'/'created' always keep the
        # SQL-level recency order, even inside a folder.
        if sort == "title" or (sort == "default" and folder is not None):
            rows = sorted(rows, key=folder_sort_key)

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
                    "occurred_at": note.occurred_at,
                    "period": note.period,
                }
            )
            if limit is not None and len(result) >= limit:
                break
        return result

    def entries_in(
        self, workspace: str, owner_id: str, start: str, end: str, folder: str | None = None
    ) -> list[dict]:
        """Notes whose temporal metadata overlaps ``[start, end)``.

        ``occurred_at`` notes (day granularity) match via an indexed ISO-date range
        query. ``period`` notes (week/month/year granularity) have no directly
        comparable column — a week key like ``2026-W12`` doesn't sort against day
        strings — so those are range-overlap tested in Python against the same
        ``[start, end)`` window, which is itself always a canonical period's own
        ``[start, next().start)``. ``folder`` deliberately uses path-boundary prefix
        semantics, unlike the older exact-folder list endpoint.
        """
        with self.timed_session() as session:
            q = select(Note).where(
                Note.workspace == workspace,
                Note.owner_id == owner_id,
                (col(Note.occurred_at) >= start) & (col(Note.occurred_at) < end)
                | col(Note.period).is_not(None),
            )
            if folder is not None:
                q = q.where(
                    (col(Note.folder) == folder)
                    | col(Note.folder).startswith(folder + "/", autoescape=True)
                )
            rows = session.exec(q).all()

        matched: list[tuple[str, Note]] = []
        for note in rows:
            if note.occurred_at is not None:
                if start <= note.occurred_at < end:
                    matched.append((note.occurred_at, note))
                continue
            if note.period is None:
                continue
            try:
                period = parse_period_key(note.period)
            except ValueError:
                continue
            period_start = period.start.isoformat()
            period_end = period.next().start.isoformat()
            if period_start < end and period_end > start:
                matched.append((period_start, note))
        matched.sort(key=lambda pair: (pair[0], pair[1].created_at))

        return [
            {
                "note_id": note.id,
                "workspace": note.workspace,
                "owner_id": note.owner_id,
                "title": note.title,
                "folder": note.folder,
                "tags": json.loads(note.tags or "[]"),
                "created_at": note.created_at,
                "updated_at": note.updated_at,
                "occurred_at": note.occurred_at,
                "period": note.period,
            }
            for _, note in matched
        ]

    def list_folders(self, workspace: str, owner_id: str) -> list[str]:
        with self.timed_session() as session:
            rows = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "SELECT DISTINCT folder FROM notes"
                    " WHERE workspace = :workspace AND owner_id = :owner_id AND folder != ''"
                ),
                {"workspace": workspace, "owner_id": owner_id},
            ).fetchall()
        return [row[0] for row in rows]

    def workspace_stats(self, owner_id: str, workspaces: list[str]) -> dict[str, dict]:
        if not workspaces:
            return {}
        with self.timed_session() as session:
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
