"""Chunk, FTS5, and vec0 repository.

All queries in this file use session.execute(text(...)) rather than session.exec()
because FTS5 and vec0 are SQLite virtual tables that do not expose a column API
compatible with SQLModel's select() builder. This is not deprecated usage —
session.exec() is the SQLModel preference for *regular* tables only.
# ty: ignore[deprecated] comments on individual execute() calls below are
suppressing a false positive from ty's SQLModel-specific deprecation rule.
"""

import json
from datetime import UTC, datetime

from nanoid import generate
from sqlalchemy import CursorResult, Engine, text
from sqlmodel import Session

from kajet_turbo.embedding.cache import pack_vector
from kajet_turbo.log import logger


class NoteChunkRepository:
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

    def replace_chunks(
        self,
        note_id: str,
        workspace: str,
        owner_id: str,
        title: str,
        chunks: list,  # list[kajet_turbo.markdown.Chunk]
        embeddings: list[list[float]] | None,
        dim: int | None,
    ) -> None:
        """Replace all chunks (and vectors) for a note. ``embeddings`` is None (chunks only
        → stale) or one vector per chunk (→ indexed, vectors into note_chunks_vec_{dim})."""
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

    def get_chunks(self, note_id: str) -> list[dict]:
        with Session(self._engine) as session:
            rows = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "SELECT id, ordinal, header_path, content, char_start, char_end, dim"
                    " FROM note_chunks WHERE note_id = :nid ORDER BY ordinal"
                ),
                {"nid": note_id},
            ).fetchall()
        return [dict(r._mapping) for r in rows]

    _CHUNK_SELECT = (
        " c.note_id AS note_id, n.title AS title, n.folder AS folder,"
        " c.header_path AS header_path, c.content AS content"
    )

    @staticmethod
    def _chunk_row(m, score):
        return {
            "note_id": m["note_id"],
            "title": m["title"],
            "folder": m["folder"],
            "header_path": json.loads(m["header_path"]),
            "content": m["content"],
            "score": score,
        }

    def search_fts(self, query: str, workspace: str, owner_id: str, limit: int = 50) -> list[dict]:
        try:
            with Session(self._engine) as session:
                rows = session.execute(  # ty: ignore[deprecated] - raw SQL
                    text(
                        f"SELECT f.chunk_id AS chunk_id,{self._CHUNK_SELECT},"
                        " bm25(notes_fts) AS rank"
                        " FROM notes_fts f"
                        " JOIN note_chunks c ON c.id = f.chunk_id"
                        " JOIN notes n ON n.id = c.note_id"
                        " WHERE notes_fts MATCH :q AND f.workspace = :ws AND n.owner_id = :o"
                        " ORDER BY rank LIMIT :limit"
                    ),
                    {"q": query, "ws": workspace, "o": owner_id, "limit": limit},
                ).fetchall()
        except Exception:
            # FTS5 MATCH raises on malformed user query syntax (the common, expected case);
            # log so a genuine DB/table error isn't indistinguishable from "no results".
            logger.warning("search_fts_failed", workspace=workspace)
            return []
        return [
            {"chunk_id": r._mapping["chunk_id"], **self._chunk_row(r._mapping, None)} for r in rows
        ]

    def search_chunks_vec(
        self, embedding: bytes, workspace: str, owner_id: str, dim: int, k: int = 50
    ) -> list[dict]:
        try:
            with Session(self._engine) as session:
                rows = session.execute(  # ty: ignore[deprecated] - raw SQL
                    text(
                        f"SELECT v.chunk_id AS chunk_id,{self._CHUNK_SELECT},"
                        " v.distance AS distance"
                        f" FROM note_chunks_vec_{int(dim)} v"
                        " JOIN note_chunks c ON c.id = v.chunk_id"
                        " JOIN notes n ON n.id = c.note_id"
                        " WHERE v.embedding MATCH :emb AND k = :k AND v.workspace = :ws"
                        "  AND n.owner_id = :o"
                        " ORDER BY v.distance"
                    ),
                    {"emb": embedding, "k": k, "ws": workspace, "o": owner_id},
                ).fetchall()
        except Exception:
            # The dim-sharded vec table is created lazily at index time; if the user has a
            # backend configured but nothing embedded at this dim yet, the table is absent —
            # degrade to FTS-only rather than crashing the search.
            logger.warning("search_chunks_vec_failed", workspace=workspace, dim=dim)
            return []
        return [
            {"chunk_id": r._mapping["chunk_id"], **self._chunk_row(r._mapping, None)} for r in rows
        ]

    def hybrid_search(
        self,
        query: str,
        workspace: str,
        owner_id: str,
        embedding: bytes | None = None,
        dim: int | None = None,
        limit: int = 10,
        per_note_cap: int = 3,
    ) -> list[dict]:
        fts = self.search_fts(query, workspace, owner_id, limit=50)
        if embedding is None or dim is None:
            # Positional RRF-style scores so the public output always carries a numeric
            # score, even in FTS-only mode (no vector backend available).
            ranked = [{**hit, "score": 1.0 / (60 + rank)} for rank, hit in enumerate(fts)]
        else:
            vec = self.search_chunks_vec(embedding, workspace, owner_id, dim=dim, k=50)
            scores: dict[str, float] = {}
            by_id: dict[str, dict] = {}
            for rank, hit in enumerate(fts):
                cid = hit["chunk_id"]
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (60 + rank)
                by_id[cid] = hit
            for rank, hit in enumerate(vec):
                cid = hit["chunk_id"]
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (60 + rank)
                by_id.setdefault(cid, hit)
            ranked = [
                {**by_id[cid], "score": s}
                for cid, s in sorted(scores.items(), key=lambda x: x[1], reverse=True)
            ]
        capped: list[dict] = []
        per_note: dict[str, int] = {}
        for hit in ranked:
            nid = str(hit["note_id"])
            if per_note.get(nid, 0) >= per_note_cap:
                continue
            per_note[nid] = per_note.get(nid, 0) + 1
            capped.append(hit)
            if len(capped) >= limit:
                break
        return [{k: v for k, v in h.items() if k != "chunk_id"} for h in capped]

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

    def delete_for_workspace(self, workspace: str, owner_id: str, session: Session) -> None:
        """Delete chunks, vec, and FTS rows for (workspace, owner_id). Uses the caller's
        session; does not commit. Must be called BEFORE NoteRepository.delete_for_workspace
        in the same session — note_chunks has an FK to notes.id with no cascade."""
        params = {"workspace": workspace, "owner_id": owner_id}
        dims = session.execute(  # ty: ignore[deprecated] - raw SQL
            text(
                "SELECT DISTINCT dim FROM note_chunks"
                " WHERE workspace = :workspace AND owner_id = :owner_id AND dim IS NOT NULL"
            ),
            params,
        ).fetchall()
        for (dim,) in dims:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    f"DELETE FROM note_chunks_vec_{int(dim)}"
                    " WHERE workspace = :workspace AND owner_id = :owner_id"
                ),
                params,
            )
        session.execute(  # ty: ignore[deprecated] - raw SQL
            text(
                "DELETE FROM notes_fts WHERE chunk_id IN ("
                " SELECT id FROM note_chunks"
                " WHERE workspace = :workspace AND owner_id = :owner_id"
                ")"
            ),
            params,
        )
        session.execute(  # ty: ignore[deprecated] - raw SQL
            text("DELETE FROM note_chunks WHERE workspace = :workspace AND owner_id = :owner_id"),
            params,
        )
