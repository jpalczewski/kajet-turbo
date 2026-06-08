import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import sqlite_vec


class Storage:
    def __init__(self, db_path: str | None = None):
        self.embedding_dim = int(os.getenv("EMBEDDING_DIM", "1536"))
        self.db_path = db_path or os.getenv("DB_PATH", "/data/kajet.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = self._connect()
        self._init_schema()
        # Migrate: add password_hash if missing (for existing DBs)
        try:
            self._conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
            self._conn.commit()
        except Exception:
            pass

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
                id            TEXT PRIMARY KEY,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                created_at    TEXT NOT NULL
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

    def create_user(self, email: str, password_hash: str) -> str:
        from nanoid import generate
        user_id = generate(size=12)
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (user_id, email, password_hash, now),
        )
        self._conn.commit()
        return user_id

    def get_user_by_email(self, email: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        return dict(row) if row else None

    def user_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

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
        tags: list[str] | None = None,
        updated_at: str = "",
    ) -> None:
        row = self._conn.execute(
            "SELECT title, tags, workspace, fts_rowid FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Note {note_id} not found")

        new_title = title if title is not None else row["title"]
        new_tags = tags if tags is not None else json.loads(row["tags"] or "[]")
        fts_rowid = row["fts_rowid"]
        workspace = row["workspace"]

        # Update the notes row (title, tags, updated_at)
        self._conn.execute(
            "UPDATE notes SET title = ?, tags = ?, updated_at = ? WHERE id = ?",
            (new_title, json.dumps(new_tags), updated_at, note_id),
        )

        # Rebuild FTS entry if title or content changed
        if title is not None or content is not None:
            # Read current FTS content before replacing
            old_fts = self._conn.execute(
                "SELECT content FROM notes_fts WHERE rowid = ?", (fts_rowid,)
            ).fetchone()
            old_content = old_fts["content"] if old_fts else ""
            new_content = content if content is not None else old_content

            self._conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (fts_rowid,))
            cur = self._conn.execute(
                "INSERT INTO notes_fts (note_id, workspace, title, content) VALUES (?, ?, ?, ?)",
                (note_id, workspace, new_title, new_content),
            )
            self._conn.execute(
                "UPDATE notes SET fts_rowid = ? WHERE id = ?",
                (cur.lastrowid, note_id),
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
                """SELECT id, workspace, title, tags, created_at, updated_at
                   FROM notes WHERE workspace = ?
                   ORDER BY updated_at DESC""",
                (workspace,),
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
            """SELECT id, workspace, title, tags, created_at, updated_at
               FROM notes WHERE workspace = ?
               ORDER BY updated_at DESC LIMIT ?""",
            (workspace, limit),
        ).fetchall()
        return [{**row, "tags": json.loads(row["tags"] or "[]")} for row in rows]

    def search_fts(self, query: str, workspace: str, limit: int = 50) -> list[dict]:
        try:
            rows = self._conn.execute(
                """SELECT n.id, n.workspace, n.title, n.tags, n.created_at, n.updated_at
                   FROM notes_fts
                   JOIN notes n ON n.fts_rowid = notes_fts.rowid
                   WHERE notes_fts MATCH ? AND n.workspace = ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, workspace, limit),
            ).fetchall()
        except Exception:
            return []
        return [{**row, "tags": json.loads(row["tags"] or "[]")} for row in rows]

    def delete_workspace_notes(self, workspace: str) -> None:
        self._conn.execute(
            "DELETE FROM notes_fts WHERE rowid IN (SELECT fts_rowid FROM notes WHERE workspace = ? AND fts_rowid IS NOT NULL)",
            (workspace,),
        )
        self._conn.execute("DELETE FROM notes WHERE workspace = ?", (workspace,))
        self._conn.commit()

    def insert_vec(
        self, note_id: str, note_rowid: int, workspace: str, embedding: bytes
    ) -> None:
        self._conn.execute(
            "INSERT INTO notes_vec (note_rowid, embedding, workspace, note_id) VALUES (?, ?, ?, ?)",
            (note_rowid, embedding, workspace, note_id),
        )
        self._conn.commit()

    def search_vec(
        self, embedding: bytes, workspace: str, k: int = 20
    ) -> list[dict]:
        rows = self._conn.execute(
            """SELECT n.id, n.workspace, n.title, n.tags, n.created_at, n.updated_at, v.distance
               FROM notes_vec v
               JOIN notes n ON n.id = v.note_id
               WHERE v.embedding MATCH ? AND k = ? AND v.workspace = ?
               ORDER BY v.distance""",
            (embedding, k, workspace),
        ).fetchall()
        return [{**row, "tags": json.loads(row["tags"] or "[]")} for row in rows]

    def hybrid_search(
        self,
        query: str,
        workspace: str,
        embedding: bytes | None = None,
        limit: int = 10,
    ) -> list[dict]:
        fts_results = self.search_fts(query, workspace, limit=50)
        if embedding is None:
            return fts_results[:limit]

        vec_results = self.search_vec(embedding, workspace, k=50)

        # RRF fusion
        scores: dict[str, float] = {}
        for rank, note in enumerate(fts_results):
            scores[note["id"]] = scores.get(note["id"], 0) + 1 / (60 + rank)
        for rank, note in enumerate(vec_results):
            scores[note["id"]] = scores.get(note["id"], 0) + 1 / (60 + rank)

        all_notes = {n["id"]: n for n in fts_results + vec_results}
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [all_notes[note_id] for note_id, _ in ranked if note_id in all_notes]

    def has_vec_index(self, workspace: str) -> bool:
        count = self._conn.execute(
            "SELECT COUNT(*) FROM notes_vec WHERE workspace = ?", (workspace,)
        ).fetchone()[0]
        return count > 0

    def save_oauth_client(
        self,
        client_id: str,
        client_secret: str,
        redirect_uris: list[str],
        created_at: str,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO oauth_clients
               (client_id, client_secret, redirect_uris, created_at)
               VALUES (?, ?, ?, ?)""",
            (client_id, client_secret, json.dumps(redirect_uris), created_at),
        )
        self._conn.commit()

    def get_oauth_client(self, client_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT client_id, client_secret, redirect_uris FROM oauth_clients WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        if row is None:
            return None
        return {**row, "redirect_uris": json.loads(row["redirect_uris"])}
