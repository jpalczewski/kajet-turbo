import json
import os
import secrets
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import sqlite_vec
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

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


class Storage:
    def __init__(self, db_path: str | None = None):
        self.embedding_dim = int(os.getenv("EMBEDDING_DIM", "1536"))
        self.db_path = db_path or os.getenv("DB_PATH", "/data/kajet.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        # Build raw connection first — sets up sqlite-vec, PRAGMAs, row_factory
        self._conn = self._connect()

        # SQLAlchemy engine that reuses the same raw connection (no second connection)
        self._engine = create_engine(
            "sqlite://",
            creator=lambda: self._conn,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )

        # Create regular tables via SQLModel metadata
        SQLModel.metadata.create_all(self._engine)

        # Create virtual tables (FTS5, vec0) via raw SQL
        self._init_virtual_tables()

        # Migration shim: add password_hash column for existing DBs
        try:
            self._conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
            self._conn.commit()
        except Exception:
            pass

    def _init_virtual_tables(self) -> None:
        self._conn.executescript(f"""
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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def create_user(self, email: str, password_hash: str) -> str:
        from nanoid import generate
        user_id = generate(size=12)
        now = datetime.now(UTC).isoformat()
        user = User(id=user_id, email=email, password_hash=password_hash, created_at=now)
        with Session(self._engine) as session:
            session.add(user)
            session.commit()
        return user_id

    def get_user_by_email(self, email: str) -> dict | None:
        with Session(self._engine) as session:
            user = session.exec(select(User).where(User.email == email)).first()
        if user is None:
            return None
        return {"id": user.id, "email": user.email, "password_hash": user.password_hash}

    def user_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # --- workspace access ---

    def record_client_authorization(self, client_id: str, user_id: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO client_authorizations (client_id, user_id) VALUES (?, ?)",
            (client_id, user_id),
        )
        self._conn.commit()

    def get_user_id_by_client(self, client_id: str) -> str | None:
        with Session(self._engine) as session:
            row = session.exec(
                select(ClientAuthorization).where(ClientAuthorization.client_id == client_id)
            ).first()
        return row.user_id if row else None

    def grant_workspace_access(self, user_id: str, workspace: str, role: str = "owner") -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO workspace_access (user_id, workspace, role) VALUES (?, ?, ?)",
            (user_id, workspace, role),
        )
        self._conn.commit()

    def list_user_workspaces(self, user_id: str) -> list[str]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(WorkspaceAccess)
                .where(WorkspaceAccess.user_id == user_id)
                .order_by(WorkspaceAccess.workspace)
            ).all()
        return [r.workspace for r in rows]

    def has_workspace_access(self, user_id: str, workspace: str) -> bool:
        with Session(self._engine) as session:
            return session.exec(
                select(WorkspaceAccess).where(
                    WorkspaceAccess.user_id == user_id,
                    WorkspaceAccess.workspace == workspace,
                )
            ).first() is not None

    def close(self) -> None:
        self._engine.dispose()
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
            "DELETE FROM notes_fts WHERE rowid IN "
            "(SELECT fts_rowid FROM notes WHERE workspace = ? AND fts_rowid IS NOT NULL)",
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

    def create_session(self, user_id: str) -> str:
        token = secrets.token_hex(32)
        expires_at = int(time.time()) + 30 * 24 * 3600
        sess = UserSession(token=token, user_id=user_id, expires_at=expires_at)
        with Session(self._engine) as session:
            session.add(sess)
            session.commit()
        return token

    def get_session_user(self, token: str) -> dict | None:
        row = self._conn.execute(
            """SELECT u.id, u.email FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token = ? AND s.expires_at > ?""",
            (token, int(time.time())),
        ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        self._conn.commit()

    def upsert_registered_client(self, client_id: str, data: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO oauth_registered_clients (client_id, data) VALUES (?, ?)",
            (client_id, data),
        )
        self._conn.commit()

    def get_all_registered_clients(self) -> list[str]:
        with Session(self._engine) as session:
            rows = session.exec(select(OAuthRegisteredClient)).all()
        return [r.data for r in rows]

    def upsert_access_token(
        self,
        token: str,
        client_id: str,
        scopes: list[str] | None,
        expires_at: int | None,
        refresh_token: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO oauth_access_tokens
               (token, client_id, scopes, expires_at, refresh_token) VALUES (?, ?, ?, ?, ?)""",
            (token, client_id, json.dumps(scopes or []), expires_at, refresh_token),
        )
        self._conn.commit()

    def upsert_refresh_token(
        self,
        token: str,
        client_id: str,
        scopes: list[str] | None,
        expires_at: int | None,
    ) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO oauth_refresh_tokens
               (token, client_id, scopes, expires_at) VALUES (?, ?, ?, ?)""",
            (token, client_id, json.dumps(scopes or []), expires_at),
        )
        self._conn.commit()

    def get_valid_access_tokens(self) -> list[dict]:
        import time
        rows = self._conn.execute(
            "SELECT token, client_id, scopes, expires_at, refresh_token FROM oauth_access_tokens"
            " WHERE expires_at IS NULL OR expires_at > ?",
            (int(time.time()),),
        ).fetchall()
        return [{**row, "scopes": json.loads(row["scopes"] or "[]")} for row in rows]

    def get_valid_refresh_tokens(self) -> list[dict]:
        import time
        rows = self._conn.execute(
            "SELECT token, client_id, scopes, expires_at FROM oauth_refresh_tokens"
            " WHERE expires_at IS NULL OR expires_at > ?",
            (int(time.time()),),
        ).fetchall()
        return [{**row, "scopes": json.loads(row["scopes"] or "[]")} for row in rows]

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
