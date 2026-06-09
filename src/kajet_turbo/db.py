import os
import sqlite3
from pathlib import Path

import sqlite_vec
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from kajet_turbo.models import (  # noqa: F401 — register models in SQLModel.metadata
    ClientAuthorization,
    Note,
    OAuthAccessToken,
    OAuthClient,
    OAuthPendingAuthorization,
    OAuthRefreshToken,
    OAuthRegisteredClient,
    User,
    UserSession,
    WorkspaceAccess,
)


class Database:
    def __init__(self, db_path: str | None = None):
        self.embedding_dim = int(os.getenv("EMBEDDING_DIM", "1536"))
        self.db_path = db_path or os.getenv("DB_PATH", "/data/kajet.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = self._connect()
        self.engine = create_engine(
            "sqlite://",
            creator=lambda: self._conn,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        self._run_migrations()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _run_migrations(self) -> None:
        alembic_ini = Path("alembic.ini")
        if not alembic_ini.exists():
            alembic_ini = Path(__file__).parents[2] / "alembic.ini"
        cfg = Config(str(alembic_ini))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{self.db_path}")
        command.upgrade(cfg, "head")

    def _init_schema(self) -> None:
        with Session(self.engine) as session:
            session.execute(text(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                    note_id   UNINDEXED,
                    workspace UNINDEXED,
                    title,
                    content,
                    tokenize='trigram'
                )
            """))
            session.execute(text(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS notes_vec USING vec0(
                    note_rowid INTEGER PRIMARY KEY,
                    embedding  float[{self.embedding_dim}],
                    workspace  TEXT partition key,
                    note_id    TEXT
                )
            """))
            session.commit()

    def close(self) -> None:
        self.engine.dispose()
        self._conn.close()
