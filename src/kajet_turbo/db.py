import os
import sqlite3
from pathlib import Path

import sqlite_vec
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from kajet_turbo.models import (  # noqa: F401 — register models in SQLModel.metadata
    ClientAuthorization,
    Note,
    OAuthAccessToken,
    OAuthClient,
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

    def _init_schema(self) -> None:
        SQLModel.metadata.create_all(self.engine)
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
        try:
            self._conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
            self._conn.commit()
        except Exception:
            pass

    def close(self) -> None:
        self.engine.dispose()
        self._conn.close()
