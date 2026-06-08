import json
import os
import sqlite3
import sqlite_vec
from pathlib import Path


class Storage:
    def __init__(self, db_path: str | None = None):
        self.embedding_dim = int(os.getenv("EMBEDDING_DIM", "1536"))
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
                embedding  float[{self.embedding_dim}],
                workspace  TEXT partition key,
                note_id    TEXT
            );
        """)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def insert_note(
        self,
        note_id: str,
        workspace: str,
        title: str,
        tags: list[str],
        created_at: str,
        updated_at: str,
        content: str,
    ) -> None:
        cur = self._conn.execute(
            "INSERT INTO notes_fts (note_id, workspace, title, content) VALUES (?, ?, ?, ?)",
            (note_id, workspace, title, content),
        )
        fts_rowid = cur.lastrowid
        self._conn.execute(
            """INSERT INTO notes (id, workspace, title, tags, created_at, updated_at, fts_rowid)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (note_id, workspace, title, json.dumps(tags), created_at, updated_at, fts_rowid),
        )
        self._conn.commit()

    def get_note(self, note_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, workspace, title, tags, created_at, updated_at FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()
        if row is None:
            return None
        return {**row, "tags": json.loads(row["tags"] or "[]")}

    def update_note(
        self,
        note_id: str,
        title: str | None = None,
        content: str | None = None,
        updated_at: str = "",
    ) -> None:
        row = self._conn.execute(
            "SELECT title, fts_rowid FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Note {note_id} not found")

        new_title = title or row["title"]
        fts_rowid = row["fts_rowid"]

        if title is not None:
            self._conn.execute(
                "UPDATE notes SET title = ?, updated_at = ? WHERE id = ?",
                (new_title, updated_at, note_id),
            )
        if content is not None or title is not None:
            self._conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (fts_rowid,))
            workspace = self._conn.execute(
                "SELECT workspace FROM notes WHERE id = ?", (note_id,)
            ).fetchone()["workspace"]
            cur = self._conn.execute(
                "INSERT INTO notes_fts (note_id, workspace, title, content) VALUES (?, ?, ?, ?)",
                (note_id, workspace, new_title, content or ""),
            )
            self._conn.execute(
                "UPDATE notes SET fts_rowid = ?, updated_at = ? WHERE id = ?",
                (cur.lastrowid, updated_at, note_id),
            )
        self._conn.commit()

    def delete_note(self, note_id: str) -> None:
        row = self._conn.execute(
            "SELECT fts_rowid FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        if row and row["fts_rowid"]:
            self._conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (row["fts_rowid"],))
        self._conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self._conn.commit()

    def list_notes(
        self,
        workspace: str,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        if tags:
            rows = self._conn.execute(
                "SELECT id, workspace, title, tags, created_at, updated_at FROM notes WHERE workspace = ? ORDER BY updated_at DESC LIMIT ?",
                (workspace, limit * 3),
            ).fetchall()
            result = []
            for row in rows:
                note_tags = json.loads(row["tags"] or "[]")
                if any(t in note_tags for t in tags):
                    result.append({**row, "tags": note_tags})
                    if len(result) >= limit:
                        break
            return result
        rows = self._conn.execute(
            "SELECT id, workspace, title, tags, created_at, updated_at FROM notes WHERE workspace = ? ORDER BY updated_at DESC LIMIT ?",
            (workspace, limit),
        ).fetchall()
        return [{**row, "tags": json.loads(row["tags"] or "[]")} for row in rows]
