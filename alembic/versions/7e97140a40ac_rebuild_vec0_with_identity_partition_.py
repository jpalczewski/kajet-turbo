"""rebuild vec0 with identity partition key and chunk_size 64

Rebuilds every ``note_chunks_vec_<dim>`` table in place:

* ``chunk_size=64`` instead of vec0's default 1024. A block is read whole regardless of
  how many of its slots are live, so at 3072 dims the default costs 12 MiB per block even
  for a workspace holding a handful of vectors (#37).
* a second partition key, ``identity``, so embeddings produced by different models never
  share a KNN scan (#51). Existing vectors are labelled from ``index_meta``, which records
  the ``(backend, model, dim)`` each owner's index was built with.

A vec0 table cannot be ALTERed, so this copies vectors out to a temp table, drops the
virtual table, recreates it with the new shape, and copies back. Verified on sqlite-vec
0.1.9: a plain ``SELECT embedding`` returns the stored blob byte for byte. Peak disk is
roughly twice the vector data; the whole rebuild runs inside Alembic's transaction.

Vectors that cannot be labelled are dropped rather than guessed at: an owner with no
``index_meta`` row, or whose active dimension differs from the shard being rebuilt, has
vectors search can never reach (it always queries the active dimension), and writing a
wrong identity would only hide them behind a partition that never matches.

Revision ID: 7e97140a40ac
Revises: 6bbe78a9932a
Create Date: 2026-09-05 12:41:07.930212

"""

import hashlib
import logging
import re
from collections.abc import Callable, Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7e97140a40ac"
down_revision: str | Sequence[str] | None = "6bbe78a9932a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the application constants: a migration describes the schema at one
# point in history, so it must not follow later edits to kajet_turbo.
CHUNK_SIZE = 64
IDENTITY_KEY_CHARS = 16

_VEC_TABLE = re.compile(r"^note_chunks_vec_(\d+)$")

# Alembic's own logger, so a dropped-vector warning lands in the migration output the
# operator is already reading rather than on a bare stdout nobody looks at.
log = logging.getLogger("alembic.runtime.migration")

_OLD_COLUMNS = "chunk_rowid, embedding, workspace, owner_id, note_id, chunk_id"
_NEW_COLUMNS = "chunk_rowid, embedding, workspace, identity, owner_id, note_id, chunk_id"


def _old_ddl(dim: int) -> str:
    return (
        " chunk_rowid INTEGER PRIMARY KEY,"
        f" embedding float[{dim}],"
        " workspace TEXT partition key,"
        " owner_id TEXT,"
        " note_id TEXT,"
        " chunk_id TEXT"
    )


def _new_ddl(dim: int) -> str:
    return (
        " chunk_rowid INTEGER PRIMARY KEY,"
        f" embedding float[{dim}],"
        " workspace TEXT partition key,"
        " identity TEXT partition key,"
        " owner_id TEXT,"
        " note_id TEXT,"
        " chunk_id TEXT,"
        f" chunk_size={CHUNK_SIZE}"
    )


def _identity_key(backend: str, model: str) -> str:
    return hashlib.sha256(f"{backend}\x00{model}".encode()).hexdigest()[:IDENTITY_KEY_CHARS]


def _vec_tables(conn) -> list[tuple[str, int]]:
    """Every real vec0 shard, as ``(table, dim)``. Matching on the SQL rather than on the
    name alone keeps vec0's shadow tables (``..._chunks``, ``..._rowids``, ...) out — they
    share the prefix but are ordinary tables."""
    rows = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
        " AND sql LIKE 'CREATE VIRTUAL TABLE%USING vec0%'"
    ).fetchall()
    return [(name, int(match.group(1))) for (name,) in rows if (match := _VEC_TABLE.match(name))]


def _rebuild(
    conn,
    table: str,
    ddl: str,
    *,
    select: str,
    columns: str,
    on_temp: Callable[[str], None] | None = None,
) -> None:
    """Copy ``select`` out of ``table``, recreate the table from ``ddl``, copy ``columns``
    back. ``on_temp`` runs against the temp table while it holds the only copy of the data —
    the one place where rows can be relabelled or discarded."""
    temp = f"{table}_rebuild"
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {temp}")
    conn.exec_driver_sql(f"CREATE TABLE {temp} AS SELECT {select} FROM {table}")
    if on_temp is not None:
        on_temp(temp)
    conn.exec_driver_sql(f"DROP TABLE {table}")
    conn.exec_driver_sql(f"CREATE VIRTUAL TABLE {table} USING vec0({ddl})")
    conn.exec_driver_sql(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {temp}")
    conn.exec_driver_sql(f"DROP TABLE {temp}")


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    identities = [
        (owner, _identity_key(backend, model), active_dim)
        for owner, backend, model, active_dim in conn.exec_driver_sql(
            "SELECT owner_id, backend, model, dim FROM index_meta"
        ).fetchall()
    ]

    for table, dim in _vec_tables(conn):

        def label(temp: str, dim: int = dim, table: str = table) -> None:
            for owner, key, active_dim in identities:
                if active_dim == dim:
                    conn.exec_driver_sql(
                        f"UPDATE {temp} SET identity = ? WHERE owner_id = ?", (key, owner)
                    )
            orphans = conn.exec_driver_sql(
                f"SELECT COUNT(*) FROM {temp} WHERE identity = ''"
            ).scalar()
            if orphans:
                log.warning(
                    "%s: dropping %d vector(s) with no resolvable index identity",
                    table,
                    orphans,
                )
                conn.exec_driver_sql(f"DELETE FROM {temp} WHERE identity = ''")

        _rebuild(
            conn,
            table,
            _new_ddl(dim),
            select=f"{_OLD_COLUMNS}, '' AS identity",
            columns=_NEW_COLUMNS,
            on_temp=label,
        )


def downgrade() -> None:
    """Downgrade schema.

    Drops the identity labels (the pre-#37 table has nowhere to keep them) and returns to
    vec0's default block size. The vectors themselves survive the round trip.
    """
    conn = op.get_bind()
    for table, dim in _vec_tables(conn):
        _rebuild(conn, table, _old_ddl(dim), select=_OLD_COLUMNS, columns=_OLD_COLUMNS)
