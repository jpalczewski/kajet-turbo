import os

import sqlite_vec
from sqlalchemy import create_engine, event, pool
from alembic import context
from sqlmodel import SQLModel

from kajet_turbo import models as _models  # noqa: F401 — registers models in SQLModel.metadata

target_metadata = SQLModel.metadata

VIRTUAL_TABLE_PREFIXES = ("notes_fts", "notes_vec")


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        return not any(name.startswith(p) for p in VIRTUAL_TABLE_PREFIXES)
    return True


def _get_url() -> str:
    url = context.config.get_main_option("sqlalchemy.url")
    if url:
        return url
    db_path = os.getenv("DB_PATH", "/data/kajet.db")
    return f"sqlite:///{db_path}"


def run_migrations_offline() -> None:
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        include_object=include_object,
        user_module_prefix="sqlmodel.sql.sqltypes.",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_get_url(), poolclass=pool.NullPool)

    @event.listens_for(connectable, "connect")
    def load_vec(dbapi_conn, _):
        dbapi_conn.enable_load_extension(True)
        sqlite_vec.load(dbapi_conn)
        dbapi_conn.enable_load_extension(False)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            include_object=include_object,
            user_module_prefix="sqlmodel.sql.sqltypes.",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
