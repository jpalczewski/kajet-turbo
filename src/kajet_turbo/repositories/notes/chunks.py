"""Chunk, FTS5, and vec0 repository.

FTS5 and vec0 queries use session.execute(text(...)) because SQLite virtual tables
do not expose a column API compatible with SQLModel's select() builder. Regular
``note_chunks`` operations use typed SQLAlchemy Core statements.
# ty: ignore[deprecated] comments on individual execute() calls below are
suppressing a false positive from ty's SQLModel-specific deprecation rule.
"""

import json
import re
from datetime import UTC, datetime

from nanoid import generate
from sqlalchemy import CursorResult, delete, text
from sqlmodel import Session, col, select

from kajet_turbo.embedding.cache import pack_vector
from kajet_turbo.log import logger
from kajet_turbo.models import NoteChunk
from kajet_turbo.perf import timed
from kajet_turbo.repositories import DbRepository

_FTS_TOKEN = re.compile(r"\w+", re.UNICODE)

# notes_fts is declared with tokenize='trigram' (see db.py). A trigram index cannot
# represent a term shorter than three characters, so such a token matches nothing on its
# own — and inside an AND chain it drops the whole result set to empty. Polish prose is
# full of them ("z", "we", "się" is fine at 3), so they are dropped when building the
# expression rather than emitted and silently poisoning the query.
_FTS_MIN_TOKEN = 3

# Polish closed-class function words (pronouns, conjunctions, prepositions, particles,
# common copula/modal forms) — length alone doesn't separate them from content words
# ("jest" is 4 letters and still near-universal). Measured against a production snapshot
# via CREATE VIRTUAL TABLE ... USING fts5vocab('notes_fts', 'row'): each of these matches
# 8-90% of all chunks under trigram tokenize, because a 3-char token is exactly one
# trigram and matches as a substring anywhere, not as a word. OR-ing several of them into
# one query forces bm25 to rank most of the table for near-zero information — content
# words measured the same way sat at 0.2-6%. Dropping them cut real fts_ms 1.8x-8x on
# stopword-heavy queries in that benchmark with the same top-50 result set (see #72).
_FTS_STOPWORDS = frozenset(
    [
        "ale",
        "bez",
        "być",
        "był",
        "było",
        "coś",
        "czy",
        "dla",
        "gdy",
        "gdzie",
        "ich",
        "ile",
        "jak",
        "jako",
        "jego",
        "jest",
        "już",
        "kto",
        "które",
        "mnie",
        "może",
        "nad",
        "nic",
        "nie",
        "niż",
        "ona",
        "one",
        "oni",
        "pod",
        "przez",
        "przy",
        "się",
        "tak",
        "tam",
        "tego",
        "ten",
        "tylko",
        "tym",
        "więc",
        "zamiast",
        "żeby",
    ]
)


def _to_fts_query(query: str) -> str:
    r"""Turn free text into a valid FTS5 MATCH expression for the lexical search leg.

    FTS5 parses its right-hand operand as a query language, not as text: ``,`` ``-``
    ``:`` ``(`` ``)`` ``"`` and a bare ``NOT`` are syntax, so ordinary prose raises
    OperationalError. Queries here are LLM-written natural language (the tool documents
    no FTS5 syntax; ``grep_notes`` covers literal search), and in production every
    comma-bearing query was failing this way — 109 times in 30 days, each one silently
    reducing the hybrid search to its vector leg.

    Tokenizing on ``\w+`` and quoting each token makes any input valid: a quoted token is
    an FTS5 string literal, so operator keywords and punctuation lose their meaning.
    Tokens cannot contain ``"`` by construction, so no inner escaping is needed.

    Terms are joined with OR, not the implicit AND the raw expression used to get.
    Measured against the real trigram index, AND returns zero rows for every
    natural-language query that fails today, so quoting alone would fix the exception and
    restore nothing; OR brings the leg back. This is the default for free-text input in
    Lucene, Elasticsearch and Tantivy, and the engines that default to AND (Typesense,
    Meilisearch) pair it with automatic term relaxation. Precision is left to bm25 — which
    ranks documents matching more terms higher, and clamps IDF at zero so common words
    cannot invert a ranking — and to the RRF fusion this feeds.

    Stopwords are dropped next, but only if at least one non-stopword token remains — a
    query that is entirely function words ("co to jest") still needs something to search
    on, so it falls back to the unfiltered set rather than becoming an empty MATCH.

    Returns ``""`` when nothing usable survives; callers must skip the query, since an
    empty MATCH expression is a syntax error in its own right.
    """
    tokens = [t for t in _FTS_TOKEN.findall(query) if len(t) >= _FTS_MIN_TOKEN]
    filtered = [t for t in tokens if t.lower() not in _FTS_STOPWORDS]
    return " OR ".join(f'"{token}"' for token in filtered or tokens)


class NoteChunkRepository(DbRepository):
    repository_name = "note_chunks"

    def ensure_vec_table(self, dim: int) -> None:
        """Lazily create the dim-sharded vec0 table for this dimension. ``dim`` MUST be a
        positive int — it is interpolated into DDL, so a non-int is rejected to keep the
        statement injection-proof."""
        if not isinstance(dim, int) or isinstance(dim, bool) or dim <= 0:
            raise ValueError(f"dim must be a positive int, got {dim!r}")
        with self.timed_session() as session:
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

    @staticmethod
    def replace_chunks_in_session(
        session: Session,
        note_id: str,
        workspace: str,
        owner_id: str,
        title: str,
        chunks: list,  # list[kajet_turbo.markdown.Chunk]
        embeddings: list[list[float]] | None,
        dim: int | None,
        *,
        expected_generation: int | None = None,
    ) -> bool:
        """Replace all chunks (and vectors) for a note, in the caller's session — does not
        commit or roll back. ``embeddings`` is None (chunks only → stale) or one vector per
        chunk (→ indexed, vectors into note_chunks_vec_{dim}).

        When ``expected_generation`` is provided, the first statement acquires SQLite's
        write lock and verifies the note revision. A superseded indexer therefore cannot
        delete or overwrite chunks produced by a newer edit, even across processes. On a
        superseded generation this returns False and leaves the session's transaction open
        with nothing applied — the caller decides whether to roll back or fold that outcome
        into a larger transaction. Returns whether the replacement was applied.
        """
        if embeddings is not None:
            if dim is None:
                raise ValueError("dim is required when embeddings are provided")
            if len(embeddings) != len(chunks):
                raise ValueError(
                    f"embeddings ({len(embeddings)}) must match chunks ({len(chunks)})"
                )
        now = datetime.now(UTC).isoformat()
        if expected_generation is not None:
            current = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "UPDATE notes SET index_state = 'stale', indexed_at = NULL"
                    " WHERE id = :nid AND index_generation = :expected"
                    " RETURNING id"
                ),
                {"nid": note_id, "expected": expected_generation},
            ).fetchone()
            if current is None:
                return False
        NoteChunkRepository.delete_chunks(note_id, session)

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
        return True

    def replace_chunks(
        self,
        note_id: str,
        workspace: str,
        owner_id: str,
        title: str,
        chunks: list,  # list[kajet_turbo.markdown.Chunk]
        embeddings: list[list[float]] | None,
        dim: int | None,
        *,
        expected_generation: int | None = None,
    ) -> bool:
        """Replace all chunks (and vectors) for a note. See ``replace_chunks_in_session`` for
        the CAS/vector-write contract; this wrapper owns the session, commits on success, and
        rolls back on a superseded generation."""
        with self.operation(
            "replace_chunks",
            note_id=note_id,
            workspace=workspace,
            owner_id=owner_id,
            chunks=len(chunks),
            vectorized=embeddings is not None,
        ) as operation:
            session = operation.session
            applied = self.replace_chunks_in_session(
                session,
                note_id,
                workspace,
                owner_id,
                title,
                chunks,
                embeddings,
                dim,
                expected_generation=expected_generation,
            )
            if not applied:
                session.rollback()
                operation.outcome = "superseded"
                return False
            session.commit()
            return True

    def attach_vectors(
        self,
        note_id: str,
        workspace: str,
        owner_id: str,
        dim: int,
        vectors: dict[str, list[float]],
    ) -> bool:
        """Attach vectors to a note's EXISTING chunk rows (deferred embedding path).
        ``vectors`` maps chunk id → vector and must cover exactly the stored chunk-id
        set, validated inside the same transaction: a mismatch means a concurrent edit
        replaced the chunks between the caller's read and this write, so the attach
        no-ops (returns False) and the note stays ``stale`` — the edit's own follow-up
        job repairs it. On success old-dim vectors are purged and the note flips to
        ``indexed``."""
        if not isinstance(dim, int) or isinstance(dim, bool) or dim <= 0:
            raise ValueError(f"dim must be a positive int, got {dim!r}")
        now = datetime.now(UTC).isoformat()
        with self.operation(
            "attach_vectors", note_id=note_id, dim=dim, chunks=len(vectors)
        ) as operation:
            session = operation.session
            rows = session.execute(  # ty: ignore[deprecated] - raw SQL
                text("SELECT id, rowid AS rowid FROM note_chunks WHERE note_id = :nid"),
                {"nid": note_id},
            ).fetchall()
            if not rows or {r._mapping["id"] for r in rows} != set(vectors):
                operation.outcome = "skipped"
                operation.add_fields(stored_chunks=len(rows))
                return False
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
            for row in rows:
                m = row._mapping
                session.execute(  # ty: ignore[deprecated] - raw SQL
                    text(
                        f"INSERT INTO note_chunks_vec_{dim}"
                        " (chunk_rowid, embedding, workspace, owner_id, note_id, chunk_id)"
                        " VALUES (:rowid, :emb, :ws, :owner, :nid, :cid)"
                    ),
                    {
                        "rowid": m["rowid"],
                        "emb": pack_vector(vectors[m["id"]]),
                        "ws": workspace,
                        "owner": owner_id,
                        "nid": note_id,
                        "cid": m["id"],
                    },
                )
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text("UPDATE note_chunks SET dim = :dim WHERE note_id = :nid"),
                {"dim": dim, "nid": note_id},
            )
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text("UPDATE notes SET index_state = 'indexed', indexed_at = :at WHERE id = :nid"),
                {"at": now, "nid": note_id},
            )
            session.commit()
            operation.outcome = "attached"
        return True

    def get_chunks(self, note_id: str) -> list[dict]:
        with self.timed_session() as session:
            rows = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "SELECT id, ordinal, header_path, content, char_start, char_end, dim"
                    " FROM note_chunks WHERE note_id = :nid ORDER BY ordinal"
                ),
                {"nid": note_id},
            ).fetchall()
        return [dict(r._mapping) for r in rows]

    @staticmethod
    def delete_chunks(note_id: str, session: Session) -> None:
        """Delete a note's chunks, FTS rows, and vectors in a caller-owned session."""
        params = {"note_id": note_id}
        dims = session.exec(
            select(NoteChunk.dim)
            .where(col(NoteChunk.note_id) == note_id, col(NoteChunk.dim).is_not(None))
            .distinct()
        ).all()
        for dim in dims:
            assert dim is not None
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(f"DELETE FROM note_chunks_vec_{int(dim)} WHERE note_id = :note_id"), params
            )
        session.execute(  # ty: ignore[deprecated] - raw SQL
            text("DELETE FROM notes_fts WHERE note_id = :note_id"), params
        )
        session.execute(  # ty: ignore[deprecated] - DELETE statement
            delete(NoteChunk).where(col(NoteChunk.note_id) == note_id)
        )

    _CHUNK_SELECT = (
        " c.note_id AS note_id, n.title AS title, n.folder AS folder, n.updated_at AS updated_at,"
        " c.header_path AS header_path, c.content AS content"
    )

    @staticmethod
    def _chunk_row(m, score):
        return {
            "note_id": m["note_id"],
            "title": m["title"],
            "folder": m["folder"],
            "updated_at": m["updated_at"],
            "header_path": json.loads(m["header_path"]),
            "content": m["content"],
            "score": score,
        }

    def search_fts(self, query: str, workspace: str, owner_id: str, limit: int = 50) -> list[dict]:
        match = _to_fts_query(query)
        if not match:
            # No usable tokens (empty, whitespace, or punctuation only). An empty MATCH
            # expression is itself a syntax error, so skip the query rather than raise.
            return []
        try:
            with self.timed_session() as session, timed("fts_ms"):
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
                    {"q": match, "ws": workspace, "o": owner_id, "limit": limit},
                ).fetchall()
        except Exception as e:
            # _to_fts_query makes any input syntactically valid, so this is no longer the
            # expected path for ordinary queries — anything landing here is a genuine
            # DB/table problem and should be read as such.
            logger.opt(exception=e).warning("search_fts_failed", workspace=workspace)
            return []
        return [
            {"chunk_id": r._mapping["chunk_id"], **self._chunk_row(r._mapping, None)} for r in rows
        ]

    def search_chunks_vec(
        self, embedding: bytes, workspace: str, owner_id: str, dim: int, k: int = 50
    ) -> list[dict]:
        try:
            with self.timed_session() as session, timed("vec_ms"):
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
        except Exception as e:
            # The dim-sharded vec table is created lazily at index time; if the user has a
            # backend configured but nothing embedded at this dim yet, the table is absent —
            # degrade to FTS-only rather than crashing the search.
            logger.opt(exception=e).warning(
                "search_chunks_vec_failed", workspace=workspace, dim=dim
            )
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
        meta_hits: list[dict] | None = None,
        allowed_note_ids: set[str] | None = None,
    ) -> list[dict]:
        candidate_limit = 200 if allowed_note_ids is not None else 50
        fts = self.search_fts(query, workspace, owner_id, limit=candidate_limit)
        vec = (
            self.search_chunks_vec(embedding, workspace, owner_id, dim=dim, k=candidate_limit)
            if embedding is not None and dim is not None
            else []
        )
        meta = meta_hits or []
        if allowed_note_ids is not None:
            fts = [h for h in fts if str(h["note_id"]) in allowed_note_ids]
            vec = [h for h in vec if str(h["note_id"]) in allowed_note_ids]
            meta = [h for h in meta if str(h["note_id"]) in allowed_note_ids]

        scores: dict[str, float] = {}
        by_id: dict[str, dict] = {}
        for candidate_list in (fts, vec):
            for rank, hit in enumerate(candidate_list):
                cid = hit["chunk_id"]
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (60 + rank)
                by_id.setdefault(cid, hit)

        # Best (highest-scoring) existing chunk per note, for meta-hit boosting below.
        best_chunk_for_note: dict[str, tuple[float, str]] = {}
        for cid, s in scores.items():
            nid = str(by_id[cid]["note_id"])
            if nid not in best_chunk_for_note or s > best_chunk_for_note[nid][0]:
                best_chunk_for_note[nid] = (s, cid)

        meta_matched: dict[str, list[str]] = {}
        for rank, hit in enumerate(meta):
            nid = str(hit["note_id"])
            boost = 1.0 / (60 + rank)
            meta_matched.setdefault(nid, []).extend(hit["matched_on"])
            if nid in best_chunk_for_note:
                cid = best_chunk_for_note[nid][1]
                scores[cid] = scores[cid] + boost
            else:
                # Note has no chunk candidate (e.g. empty content never produced chunks) —
                # synthesize a note-level row so it still surfaces instead of being dropped.
                synthetic_id = f"meta:{nid}"
                scores[synthetic_id] = scores.get(synthetic_id, 0.0) + boost
                by_id.setdefault(
                    synthetic_id,
                    {
                        "note_id": nid,
                        "title": hit["title"],
                        "folder": hit["folder"],
                        "updated_at": hit["updated_at"],
                        "header_path": [],
                        "content": "",
                    },
                )

        ranked = [
            {**by_id[cid], "score": s, "matched_on": meta_matched.get(str(by_id[cid]["note_id"]))}
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
        with self.timed_session() as session:
            row = session.execute(  # ty: ignore[deprecated] - raw SQL
                text("SELECT backend, model, dim FROM index_meta WHERE owner_id = :o"),
                {"o": owner_id},
            ).fetchone()
        return dict(row._mapping) if row else None

    def upsert_index_meta(self, owner_id: str, backend: str, model: str, dim: int) -> None:
        now = datetime.now(UTC).isoformat()
        with self.operation(
            "upsert_index_meta", owner_id=owner_id, model=model, dim=dim
        ) as operation:
            session = operation.session
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

    def delete_for_workspace_in_session(
        self, workspace: str, owner_id: str, session: Session
    ) -> None:
        """Delete chunks, vec, and FTS rows for (workspace, owner_id). Uses the caller's
        session; does not commit. Must be called BEFORE
        NoteRepository.delete_for_workspace_in_session in the same session — note_chunks has
        an FK to notes.id with no cascade."""
        params = {"workspace": workspace, "owner_id": owner_id}
        dims = session.exec(
            select(NoteChunk.dim)
            .where(
                col(NoteChunk.workspace) == workspace,
                col(NoteChunk.owner_id) == owner_id,
                col(NoteChunk.dim).is_not(None),
            )
            .distinct()
        ).all()
        for dim in dims:
            assert dim is not None
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
        session.execute(  # ty: ignore[deprecated] - DELETE statement
            delete(NoteChunk).where(
                col(NoteChunk.workspace) == workspace,
                col(NoteChunk.owner_id) == owner_id,
            )
        )
