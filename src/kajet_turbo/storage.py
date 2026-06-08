import os
import sqlite3
import sqlite_vec
from pathlib import Path

EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


class Storage:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.getenv("DB_PATH", "/data/kajet.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = self._connect()
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
        self._conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id     TEXT PRIMARY KEY,
                client_secret TEXT NOT NULL,
                redirect_uris TEXT NOT NULL,
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id         TEXT PRIMARY KEY,
                email      TEXT UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS workspace_access (
                user_id   TEXT NOT NULL REFERENCES users(id),
                workspace TEXT NOT NULL,
                role      TEXT NOT NULL DEFAULT 'owner',
                PRIMARY KEY (user_id, workspace)
            );

            CREATE TABLE IF NOT EXISTS notes (
                id         TEXT PRIMARY KEY,
                workspace  TEXT NOT NULL,
                title      TEXT NOT NULL,
                tags       TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                fts_rowid  INTEGER
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                note_id   UNINDEXED,
                workspace UNINDEXED,
                title,
                content,
                tokenize='trigram'
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS notes_vec USING vec0(
                note_rowid INTEGER PRIMARY KEY,
                embedding  float[{EMBEDDING_DIM}],
                workspace  TEXT partition key,
                note_id    TEXT
            );
        """)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
