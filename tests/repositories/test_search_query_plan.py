"""Shape of the two search queries (#264).

Both legs materialize their MATCH before joining. Without that, SQLite drives the join from
`notes` and scans it in full — on production that took the vector leg from 17 ms of actual
KNN to 333 ms, and made the lexical leg build an AUTOMATIC COVERING INDEX on every call.

What these tests can and cannot pin down: the planner's choices depend on table sizes (with
no ANALYZE, SQLite estimates from page counts), so on a five-note fixture a full scan of
`notes` is genuinely the cheapest plan and asserting against it would be asserting a lie.
What is ours to guarantee is the SQL: the CTE must be MATERIALIZED, so the MATCH runs once
and the planner cannot flatten it back into the join. The rest of these tests cover the
semantics that the rewrite could plausibly have broken.
"""

import pytest
from sqlalchemy import text
from sqlmodel import Session

from kajet_turbo.embedding.cache import pack_vector
from kajet_turbo.markdown import Chunk
from kajet_turbo.models import Note
from kajet_turbo.repositories.notes import NoteChunkRepository
from tests.helpers import vec_identity


def _plan(database, sql: str, params: dict) -> list[str]:
    with Session(database.engine) as session:
        rows = session.execute(  # ty: ignore[deprecated] - raw SQL, as in the repository
            text(f"EXPLAIN QUERY PLAN {sql}"), params
        ).fetchall()
    return [r[3] for r in rows]


def _note(session, note_id: str, owner: str, title: str, workspace: str = "ws") -> None:
    session.add(
        Note(
            id=note_id,
            workspace=workspace,
            owner_id=owner,
            title=title,
            folder="",
            created_at="2026-01-01",
            updated_at="2026-01-01",
        )
    )


@pytest.fixture
def two_owners(database):
    """Two owners with a workspace of the SAME name — workspace names are per-owner, not
    globally unique, which is what makes the placement of LIMIT load-bearing.

    Titles are equal-length on purpose: bm25 rewards shorter documents, so "Mine 0" vs
    "Theirs 0" would let this owner win the ranking on its own and the test would pass for
    the wrong reason. With the ranks tied, rowid order decides — and the other owner holds
    the lower ones."""
    repo = NoteChunkRepository(database.engine)
    identity = vec_identity(2)
    repo.ensure_vec_table(identity)
    with Session(database.engine) as session:
        for i in range(3):
            _note(session, f"mine{i}", "u1", f"Axx {i}")
        for i in range(6):
            _note(session, f"theirs{i}", "u2", f"Bxx {i}")
        session.commit()
    # The other owner's chunks are written FIRST, so they hold the lower rowids and win any
    # tie on bm25 rank. A LIMIT applied before the owner filter would be spent entirely on
    # them — which is exactly the regression these tests exist to catch.
    for i in range(6):
        repo.replace_chunks(
            f"theirs{i}",
            "ws",
            "u2",
            f"Bxx {i}",
            [Chunk(0, [f"# Bxx {i}"], "apple pie recipe", 0, 16)],
            [[1.0, float(i)]],
            identity,
        )
    for i in range(3):
        repo.replace_chunks(
            f"mine{i}",
            "ws",
            "u1",
            f"Axx {i}",
            [Chunk(0, [f"# Axx {i}"], "apple pie recipe", 0, 16)],
            [[1.0, float(i)]],
            identity,
        )
    return repo, identity


def test_vector_search_materializes_the_match(database, two_owners):
    repo, identity = two_owners
    steps = _plan(
        database,
        repo._vec_search_sql(identity.dim),
        {"emb": pack_vector([1.0, 0.0]), "k": 10, "ws": "ws", "ident": identity.key, "o": "u1"},
    )
    assert any("MATERIALIZE" in s for s in steps), steps


def test_fts_search_materializes_the_match(database, two_owners):
    repo, _ = two_owners
    steps = _plan(
        database, repo._FTS_SEARCH_SQL, {"q": '"apple"', "ws": "ws", "o": "u1", "limit": 10}
    )
    assert any("MATERIALIZE" in s for s in steps), steps


def test_fts_limit_is_not_spent_on_another_owners_chunks(database, two_owners):
    """The other owner has twice as many equally-matching chunks in a same-named workspace.
    If LIMIT were applied inside the CTE, it would be filled with their rows and this owner
    would come back short (or empty) after the owner filter."""
    repo, _ = two_owners

    hits = repo.search_fts("apple", "ws", "u1", limit=3)

    assert len(hits) == 3
    assert {h["note_id"] for h in hits} == {"mine0", "mine1", "mine2"}


def test_vector_search_returns_only_the_querying_owners_chunks(database, two_owners):
    repo, identity = two_owners

    hits = repo.search_chunks_vec(pack_vector([1.0, 0.0]), "ws", "u1", identity=identity, k=10)

    assert {h["note_id"] for h in hits} == {"mine0", "mine1", "mine2"}


def test_vector_search_orders_by_distance(database, two_owners):
    """The ORDER BY moved from the vec table's own column to the CTE's — the ranking has to
    survive that."""
    repo, identity = two_owners

    hits = repo.search_chunks_vec(pack_vector([1.0, 0.0]), "ws", "u1", identity=identity, k=10)

    assert [h["note_id"] for h in hits] == ["mine0", "mine1", "mine2"]
