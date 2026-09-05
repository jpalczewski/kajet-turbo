"""The #37 rebuild of note_chunks_vec_<dim>: chunk_size=64 + identity partition key.

The shared DB template is migrated while empty, so the migration never sees a vector
there. These tests build a table in the pre-#37 shape by hand, run the migration's own
upgrade()/downgrade() against it, and check what happened to the data.
"""

import importlib.util
import struct
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from kajet_turbo.embedding.identity import IndexIdentity

_MIGRATION = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "7e97140a40ac_rebuild_vec0_with_identity_partition_.py"
)

OLD_COLUMNS = "chunk_rowid, embedding, workspace, owner_id, note_id, chunk_id"


@pytest.fixture
def migration():
    spec = importlib.util.spec_from_file_location("vec_rebuild_migration", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pack(values: list[float]) -> bytes:
    return struct.pack(f"{len(values)}f", *values)


def _old_table(conn, dim: int) -> None:
    conn.execute(
        text(
            f"CREATE VIRTUAL TABLE note_chunks_vec_{dim} USING vec0("
            " chunk_rowid INTEGER PRIMARY KEY,"
            f" embedding float[{dim}],"
            " workspace TEXT partition key,"
            " owner_id TEXT,"
            " note_id TEXT,"
            " chunk_id TEXT"
            ")"
        )
    )


def _insert_old(conn, dim: int, rowid: int, owner: str, vector: list[float]) -> None:
    conn.execute(
        text(
            f"INSERT INTO note_chunks_vec_{dim} ({OLD_COLUMNS})"
            " VALUES (:rowid, :emb, 'ws', :owner, :nid, :cid)"
        ),
        {
            "rowid": rowid,
            "emb": _pack(vector),
            "owner": owner,
            "nid": f"n{rowid}",
            "cid": f"c{rowid}",
        },
    )


def _index_meta(conn, owner: str, backend: str, model: str, dim: int) -> None:
    conn.execute(
        text(
            "INSERT INTO index_meta (owner_id, backend, model, dim, updated_at)"
            " VALUES (:o, :b, :m, :d, '2026-01-01')"
        ),
        {"o": owner, "b": backend, "m": model, "d": dim},
    )


def _run(conn, migration, direction: str) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        getattr(migration, direction)()


def _ddl(conn, dim: int) -> str:
    return conn.execute(
        text("SELECT sql FROM sqlite_master WHERE name = :n"),
        {"n": f"note_chunks_vec_{dim}"},
    ).scalar_one()


def test_upgrade_relabels_vectors_and_applies_new_shape(database, migration):
    with database.engine.begin() as conn:
        _old_table(conn, 2)
        _index_meta(conn, "u1", "http://backend", "model-a", 2)
        _insert_old(conn, 2, 1, "u1", [1.0, 0.0])
        _insert_old(conn, 2, 2, "u1", [0.0, 1.0])

        _run(conn, migration, "upgrade")

        ddl = _ddl(conn, 2)
        assert "identity TEXT partition key" in ddl
        assert f"chunk_size={migration.CHUNK_SIZE}" in ddl

        rows = conn.execute(
            text("SELECT chunk_rowid, identity, embedding FROM note_chunks_vec_2 ORDER BY 1")
        ).fetchall()

    expected = IndexIdentity(backend="http://backend", model="model-a", dim=2).key
    assert [r[0] for r in rows] == [1, 2]
    assert {r[1] for r in rows} == {expected}
    # The blob has to survive the round trip untouched — a re-embed is not an option here.
    assert rows[0][2] == _pack([1.0, 0.0])


def test_upgrade_drops_vectors_with_no_resolvable_identity(database, migration):
    """An owner with no index_meta row, or one active at another dimension, has vectors
    search can never reach. They are dropped rather than labelled with a guess."""
    with database.engine.begin() as conn:
        _old_table(conn, 2)
        _index_meta(conn, "u1", "http://backend", "model-a", 2)
        _index_meta(conn, "u2", "http://backend", "model-a", 768)  # active elsewhere
        _insert_old(conn, 2, 1, "u1", [1.0, 0.0])
        _insert_old(conn, 2, 2, "u2", [0.0, 1.0])
        _insert_old(conn, 2, 3, "u3", [1.0, 1.0])  # no index_meta at all

        _run(conn, migration, "upgrade")

        rows = conn.execute(text("SELECT chunk_rowid, owner_id FROM note_chunks_vec_2")).fetchall()

    assert rows == [(1, "u1")]


def test_upgrade_leaves_shadow_tables_alone(database, migration):
    """vec0's shadow tables share the note_chunks_vec_ prefix; only the virtual table
    itself may be rebuilt."""
    with database.engine.begin() as conn:
        _old_table(conn, 2)
        _index_meta(conn, "u1", "http://backend", "model-a", 2)
        _insert_old(conn, 2, 1, "u1", [1.0, 0.0])

        _run(conn, migration, "upgrade")

        names = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE name LIKE 'note_chunks_vec_%'")
            )
        }

    assert "note_chunks_vec_2_rowids" in names
    assert not any(n.endswith("_rebuild") for n in names)


def test_downgrade_restores_the_old_shape_and_keeps_vectors(database, migration):
    with database.engine.begin() as conn:
        _old_table(conn, 2)
        _index_meta(conn, "u1", "http://backend", "model-a", 2)
        _insert_old(conn, 2, 1, "u1", [1.0, 0.0])

        _run(conn, migration, "upgrade")
        _run(conn, migration, "downgrade")

        ddl = _ddl(conn, 2)
        rows = conn.execute(text("SELECT chunk_rowid, embedding FROM note_chunks_vec_2")).fetchall()

    assert "identity" not in ddl
    assert "chunk_size" not in ddl
    assert rows == [(1, _pack([1.0, 0.0]))]


def test_upgrade_is_a_noop_without_vec_tables(database, migration):
    """The common case on a fresh install: nothing has been embedded yet, so no shard
    exists to rebuild."""
    with database.engine.begin() as conn:
        _run(conn, migration, "upgrade")
