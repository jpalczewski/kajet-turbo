"""Chunk, FTS5, and vec0 repository.

FTS5 and vec0 queries use raw SQL because SQLite virtual tables do not expose a column API
compatible with SQLModel's select() builder. Every ordinary-table operation uses typed
SQLModel/SQLAlchemy Core; every virtual-table statement goes through ``_raw_execute``.
"""

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from nanoid import generate
from sqlalchemy import delete, text, update
from sqlmodel import Session, col, select

from kajet_turbo.embedding.cache import pack_vector
from kajet_turbo.embedding.identity import IndexIdentity
from kajet_turbo.log import logger
from kajet_turbo.markdown import Chunk
from kajet_turbo.models import Note, NoteChunk
from kajet_turbo.perf import timed
from kajet_turbo.repositories import DbRepository
from kajet_turbo.repositories.notes.types import ChunkHit, StoredChunk

# vec0 reads a whole block per query regardless of how many of its slots are live, so the
# block size is the unit of wasted I/O. At 1024 (the vec0 default) a 3072-dim block is
# 12 MiB, so a workspace holding 26 vectors costs as much to scan as one holding 1024 —
# 120 MiB allocated for 42 MiB of live vectors, measured on production 2026-09-05. 64 keeps
# the tail workspaces proportional without fragmenting the large ones (#37).
VEC_CHUNK_SIZE = 64


def _validated_dim(dim: int) -> int:
    """Guard a dimension before it is interpolated into vec0 DDL or a table name."""
    if not isinstance(dim, int) or isinstance(dim, bool) or dim <= 0:
        raise ValueError(f"dim must be a positive int, got {dim!r}")
    return dim


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

    def ensure_vec_table(self, identity: IndexIdentity) -> None:
        """Lazily create the dim-sharded vec0 table for this identity's dimension.

        IF NOT EXISTS means this never alters a table that already exists: the shape below
        reaches an existing database ONLY through the Alembic rebuild that introduced it
        (``identity`` partition key + ``chunk_size=64``). Changing this DDL therefore needs
        a matching migration, or old and new databases silently diverge.

        ``dim`` MUST be a positive int — it is interpolated into DDL, so a non-int is
        rejected to keep the statement injection-proof.
        """
        dim = _validated_dim(identity.dim)
        with self.timed_session() as session:
            self._raw_execute(
                session,
                text(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS note_chunks_vec_{dim} USING vec0("
                    " chunk_rowid INTEGER PRIMARY KEY,"
                    f" embedding float[{dim}],"
                    " workspace TEXT partition key,"
                    " identity TEXT partition key,"
                    " owner_id TEXT,"
                    " note_id TEXT,"
                    " chunk_id TEXT,"
                    f" chunk_size={VEC_CHUNK_SIZE}"
                    ")"
                ),
            )
            session.commit()

    @staticmethod
    def _insert_vector_in_session(
        session: Session,
        identity: IndexIdentity,
        *,
        chunk_rowid: int,
        workspace: str,
        owner_id: str,
        note_id: str,
        chunk_id: str,
        vector: list[float],
    ) -> None:
        dim = _validated_dim(identity.dim)
        DbRepository._raw_execute(
            session,
            text(
                f"INSERT INTO note_chunks_vec_{dim}"
                " (chunk_rowid, embedding, workspace, identity, owner_id, note_id, chunk_id)"
                " VALUES (:rowid, :emb, :ws, :ident, :owner, :nid, :cid)"
            ),
            {
                "rowid": chunk_rowid,
                "emb": pack_vector(vector),
                "ws": workspace,
                "ident": identity.key,
                "owner": owner_id,
                "nid": note_id,
                "cid": chunk_id,
            },
        )

    @staticmethod
    def replace_chunks_in_session(
        session: Session,
        note_id: str,
        workspace: str,
        owner_id: str,
        title: str,
        chunks: list[Chunk],
        embeddings: list[list[float]] | None,
        identity: IndexIdentity | None,
        *,
        expected_generation: int | None = None,
    ) -> bool:
        """Replace all chunks (and vectors) for a note, in the caller's session — does not
        commit or roll back. ``embeddings`` is None (chunks only → stale) or one vector per
        chunk (→ indexed, vectors into note_chunks_vec_{identity.dim} under that
        identity's partition).

        When ``expected_generation`` is provided, the first statement acquires SQLite's
        write lock and verifies the note revision. A superseded indexer therefore cannot
        delete or overwrite chunks produced by a newer edit, even across processes. On a
        superseded generation this returns False and leaves the session's transaction open
        with nothing applied — the caller decides whether to roll back or fold that outcome
        into a larger transaction. Returns whether the replacement was applied.
        """
        if embeddings is not None:
            if identity is None:
                raise ValueError("identity is required when embeddings are provided")
            if len(embeddings) != len(chunks):
                raise ValueError(
                    f"embeddings ({len(embeddings)}) must match chunks ({len(chunks)})"
                )
        # None unless this call vectorises: note_chunks.dim records the shard a chunk's
        # vector went into, and a chunks-only write leaves the note stale with no vector.
        chunk_dim = identity.dim if embeddings is not None and identity is not None else None
        now = datetime.now(UTC).isoformat()
        if expected_generation is not None:
            current = session.exec(
                update(Note)
                .where(col(Note.id) == note_id, col(Note.index_generation) == expected_generation)
                .values(index_state="stale", indexed_at=None)
                .returning(col(Note.id))
            ).first()
            if current is None:
                return False
        NoteChunkRepository.delete_chunks_in_session(session, note_id)

        for i, chunk in enumerate(chunks):
            chunk_id = generate(size=12)
            row = NoteChunk(
                id=chunk_id,
                note_id=note_id,
                workspace=workspace,
                owner_id=owner_id,
                ordinal=chunk.ordinal,
                header_path=json.dumps(chunk.header_path),
                content=chunk.content,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                dim=chunk_dim,
                created_at=now,
            )
            session.add(row)
            session.flush()
            assert row.chunk_rowid is not None
            if embeddings is not None:
                assert identity is not None  # validated above; narrows for the table name
                NoteChunkRepository._insert_vector_in_session(
                    session,
                    identity,
                    chunk_rowid=row.chunk_rowid,
                    workspace=workspace,
                    owner_id=owner_id,
                    note_id=note_id,
                    chunk_id=chunk_id,
                    vector=embeddings[i],
                )
            DbRepository._raw_execute(
                session,
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
        session.exec(
            update(Note)
            .where(col(Note.id) == note_id)
            .values(index_state=state, indexed_at=now if embeddings is not None else None)
        )
        return True

    def replace_chunks(
        self,
        note_id: str,
        workspace: str,
        owner_id: str,
        title: str,
        chunks: list[Chunk],
        embeddings: list[list[float]] | None,
        identity: IndexIdentity | None,
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
                identity,
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
        identity: IndexIdentity,
        vectors: dict[str, list[float]],
    ) -> bool:
        """Attach vectors to a note's EXISTING chunk rows (deferred embedding path).
        ``vectors`` maps chunk id → vector and must cover exactly the stored chunk-id
        set, validated inside the same transaction: a mismatch means a concurrent edit
        replaced the chunks between the caller's read and this write, so the attach
        no-ops (returns False) and the note stays ``stale`` — the edit's own follow-up
        job repairs it. On success old-dim vectors are purged and the note flips to
        ``indexed``."""
        dim = _validated_dim(identity.dim)
        now = datetime.now(UTC).isoformat()
        with self.operation(
            "attach_vectors", note_id=note_id, dim=dim, identity=identity.key, chunks=len(vectors)
        ) as operation:
            session = operation.session
            rows = session.exec(
                select(NoteChunk.id, NoteChunk.chunk_rowid).where(NoteChunk.note_id == note_id)
            ).all()
            if not rows or {chunk_id for chunk_id, _ in rows} != set(vectors):
                operation.outcome = "skipped"
                operation.add_fields(stored_chunks=len(rows))
                return False
            self._delete_vectors_for_note_in_session(session, note_id)
            for chunk_id, chunk_rowid in rows:
                assert chunk_rowid is not None
                self._insert_vector_in_session(
                    session,
                    identity,
                    chunk_rowid=chunk_rowid,
                    workspace=workspace,
                    owner_id=owner_id,
                    note_id=note_id,
                    chunk_id=chunk_id,
                    vector=vectors[chunk_id],
                )
            session.exec(update(NoteChunk).where(col(NoteChunk.note_id) == note_id).values(dim=dim))
            session.exec(
                update(Note)
                .where(col(Note.id) == note_id)
                .values(index_state="indexed", indexed_at=now)
            )
            session.commit()
            operation.outcome = "attached"
        return True

    def get_chunks(self, note_id: str) -> list[StoredChunk]:
        with self.timed_session() as session:
            rows = session.exec(
                select(NoteChunk)
                .where(col(NoteChunk.note_id) == note_id)
                .order_by(col(NoteChunk.ordinal))
            ).all()
        return [
            StoredChunk(
                id=row.id,
                ordinal=row.ordinal,
                header_path=json.loads(row.header_path),
                content=row.content,
                char_start=row.char_start,
                char_end=row.char_end,
                dim=row.dim,
            )
            for row in rows
        ]

    @staticmethod
    def _delete_vectors_for_note_in_session(session: Session, note_id: str) -> None:
        dims = session.exec(
            select(NoteChunk.dim)
            .where(col(NoteChunk.note_id) == note_id, col(NoteChunk.dim).is_not(None))
            .distinct()
        ).all()
        for dim in dims:
            assert dim is not None
            DbRepository._raw_execute(
                session,
                text(f"DELETE FROM note_chunks_vec_{_validated_dim(dim)} WHERE note_id = :note_id"),
                {"note_id": note_id},
            )

    @staticmethod
    def delete_chunks_in_session(session: Session, note_id: str) -> None:
        """Delete a note's chunks, FTS rows, and vectors in a caller-owned session."""
        NoteChunkRepository._delete_vectors_for_note_in_session(session, note_id)
        DbRepository._raw_execute(
            session, text("DELETE FROM notes_fts WHERE note_id = :note_id"), {"note_id": note_id}
        )
        session.exec(delete(NoteChunk).where(col(NoteChunk.note_id) == note_id))

    _CHUNK_SELECT = (
        " c.note_id AS note_id, n.title AS title, n.folder AS folder, n.updated_at AS updated_at,"
        " c.header_path AS header_path, c.content AS content"
    )

    _FTS_SEARCH_SQL = (
        "WITH hits AS MATERIALIZED ("
        "  SELECT chunk_id, bm25(notes_fts) AS rank"
        "  FROM notes_fts WHERE notes_fts MATCH :q AND workspace = :ws"
        ")"
        f" SELECT h.chunk_id AS chunk_id,{_CHUNK_SELECT},"
        " h.rank AS rank"
        " FROM hits h"
        " JOIN note_chunks c ON c.id = h.chunk_id"
        " JOIN notes n ON n.id = c.note_id"
        " WHERE n.owner_id = :o"
        " ORDER BY h.rank LIMIT :limit"
    )

    # Both search legs materialize their MATCH before joining. Without the CTE — or with a
    # plain (non-MATERIALIZED) one, which SQLite is free to flatten — the planner drives the
    # join from `notes` and scans it in full: measured on production, the vector leg went
    # 17 ms of actual KNN to 333 ms, and the lexical leg built an AUTOMATIC COVERING INDEX
    # on every call. Materializing first costs nothing and pins the join order (#264).
    #
    # The owner predicate stays on `notes` in both, because that is the authoritative
    # record of who owns a note. The vector leg repeats it inside the CTE, where vec0 can
    # apply it as a metadata filter — a pre-filter for speed, not the check that matters.
    # The lexical leg cannot: `notes_fts` has no owner column, which is also why its LIMIT
    # has to stay outside the CTE. Workspace names are not unique across owners, so
    # limiting before the owner filter would let one owner's chunks consume another's slots.

    @staticmethod
    def _vec_search_sql(dim: int) -> str:
        return (
            "WITH hits AS MATERIALIZED ("
            "  SELECT chunk_id, distance"
            f"  FROM note_chunks_vec_{dim}"
            "  WHERE embedding MATCH :emb AND k = :k AND workspace = :ws"
            "   AND identity = :ident AND owner_id = :o"
            ")"
            f" SELECT h.chunk_id AS chunk_id,{NoteChunkRepository._CHUNK_SELECT},"
            " h.distance AS distance"
            " FROM hits h"
            " JOIN note_chunks c ON c.id = h.chunk_id"
            " JOIN notes n ON n.id = c.note_id"
            " WHERE n.owner_id = :o"
            " ORDER BY h.distance"
        )

    @staticmethod
    def _chunk_hit(row: Mapping[str, object]) -> ChunkHit:
        return ChunkHit(
            chunk_id=cast(str, row["chunk_id"]),
            note_id=cast(str, row["note_id"]),
            title=cast(str, row["title"]),
            folder=cast(str, row["folder"]),
            updated_at=cast(str, row["updated_at"]),
            header_path=cast(list[str], json.loads(cast(str, row["header_path"]))),
            content=cast(str, row["content"]),
        )

    def search_fts(
        self, query: str, workspace: str, owner_id: str, limit: int = 50
    ) -> list[ChunkHit]:
        match = _to_fts_query(query)
        if not match:
            # No usable tokens (empty, whitespace, or punctuation only). An empty MATCH
            # expression is itself a syntax error, so skip the query rather than raise.
            return []
        try:
            with self.timed_session() as session, timed("fts_ms"):
                rows = self._raw_execute(
                    session,
                    text(self._FTS_SEARCH_SQL),
                    {"q": match, "ws": workspace, "o": owner_id, "limit": limit},
                ).fetchall()
        except Exception as e:
            # _to_fts_query makes any input syntactically valid, so this is no longer the
            # expected path for ordinary queries — anything landing here is a genuine
            # DB/table problem and should be read as such.
            logger.opt(exception=e).warning("search_fts_failed", workspace=workspace)
            return []
        return [self._chunk_hit(cast(Mapping[str, object], row._mapping)) for row in rows]

    def search_chunks_vec(
        self, embedding: bytes, workspace: str, owner_id: str, identity: IndexIdentity, k: int = 50
    ) -> list[ChunkHit]:
        """KNN within one vector space. Both partition keys are constrained, so vectors
        written under another identity are never scanned, let alone ranked — embeddings
        from two models are not comparable and mixing them returns nonsense."""
        try:
            with self.timed_session() as session, timed("vec_ms"):
                rows = self._raw_execute(
                    session,
                    text(self._vec_search_sql(_validated_dim(identity.dim))),
                    {
                        "emb": embedding,
                        "k": k,
                        "ws": workspace,
                        "ident": identity.key,
                        "o": owner_id,
                    },
                ).fetchall()
        except Exception as e:
            # The dim-sharded vec table is created lazily at index time; if the user has a
            # backend configured but nothing embedded at this dim yet, the table is absent —
            # degrade to FTS-only rather than crashing the search.
            logger.opt(exception=e).warning(
                "search_chunks_vec_failed",
                workspace=workspace,
                dim=identity.dim,
                identity=identity.key,
            )
            return []
        return [self._chunk_hit(cast(Mapping[str, object], row._mapping)) for row in rows]

    @staticmethod
    def delete_for_workspace_in_session(session: Session, workspace: str, owner_id: str) -> None:
        """Delete chunks, vec, and FTS rows for (workspace, owner_id). Uses the caller's
        session; does not commit. Must be called BEFORE
        NoteRepository.delete_for_workspace_in_session in the same session — note_chunks has
        an FK to notes.id with no cascade."""
        params: dict[str, object] = {"workspace": workspace, "owner_id": owner_id}
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
            DbRepository._raw_execute(
                session,
                text(
                    f"DELETE FROM note_chunks_vec_{_validated_dim(dim)}"
                    " WHERE workspace = :workspace AND owner_id = :owner_id"
                ),
                params,
            )
        DbRepository._raw_execute(
            session,
            text(
                "DELETE FROM notes_fts WHERE chunk_id IN ("
                " SELECT id FROM note_chunks"
                " WHERE workspace = :workspace AND owner_id = :owner_id"
                ")"
            ),
            params,
        )
        session.exec(
            delete(NoteChunk).where(
                col(NoteChunk.workspace) == workspace,
                col(NoteChunk.owner_id) == owner_id,
            )
        )
