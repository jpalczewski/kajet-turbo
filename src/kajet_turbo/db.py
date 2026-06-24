import os
import sqlite3
from pathlib import Path

import sqlite_vec
from alembic.config import Config
from sqlalchemy import event, text
from sqlalchemy.pool import QueuePool
from sqlmodel import Session, create_engine

from alembic import command
from kajet_turbo.models import (  # noqa: F401 — register models in SQLModel.metadata
    ClientAuthorization,
    EmbeddingCache,
    EmbeddingProfile,
    IndexMeta,
    Note,
    NoteChunk,
    NoteLink,
    NoteTag,
    OAuthAccessToken,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthPendingAuthorization,
    OAuthRefreshToken,
    OAuthRegisteredClient,
    Tag,
    User,
    UserSession,
    WorkspaceAccess,
    WorkspaceMeta,
)


class Database:
    def __init__(self, db_path: str | None = None):
        self.embedding_dim = int(os.getenv("EMBEDDING_DIM", "1536"))
        self.db_path = db_path or os.getenv("DB_PATH", "/data/kajet.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        # QueuePool: up to pool_size connections kept warm, each thread gets
        # its own checkout. WAL mode allows concurrent reads across connections.
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=5,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(self.engine, "connect")
        def _configure(conn: sqlite3.Connection, _record) -> None:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")

        self._run_migrations()
        self._init_schema()

    def _run_migrations(self) -> None:
        alembic_ini = Path("alembic.ini")
        if not alembic_ini.exists():
            alembic_ini = Path(__file__).parents[2] / "alembic.ini"
        cfg = Config(str(alembic_ini))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{self.db_path}")
        command.upgrade(cfg, "head")

    def _init_schema(self) -> None:
        with Session(self.engine) as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text("""
                CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                    chunk_id    UNINDEXED,
                    note_id     UNINDEXED,
                    workspace   UNINDEXED,
                    title,
                    header_path,
                    content,
                    tokenize='trigram'
                )
            """)
            )
            session.commit()

    def close(self) -> None:
        self.engine.dispose()
