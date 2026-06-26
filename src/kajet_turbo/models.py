from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, LargeBinary, Text, text
from sqlmodel import Field, SQLModel


class OAuthClient(SQLModel, table=True):
    __tablename__ = "oauth_clients"

    client_id: str = Field(primary_key=True)
    client_secret: str
    redirect_uris: str = Field(sa_column=Column(Text, nullable=False))
    created_at: str


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(primary_key=True)
    email: str = Field(sa_column=Column(Text, unique=True, nullable=False))
    password_hash: str | None = None
    created_at: str


class WorkspaceAccess(SQLModel, table=True):
    __tablename__ = "workspace_access"

    user_id: str = Field(
        sa_column=Column(Text, ForeignKey("users.id"), primary_key=True, nullable=False)
    )
    workspace: str = Field(primary_key=True)
    role: str = Field(default="owner")


class ActiveWorkspace(SQLModel, table=True):
    """Per-user active workspace, persisted so it survives MCP session churn.

    The claude.ai/mobile connector opens a fresh MCP session per tool call, so
    the in-memory session state (ctx.set_state) is lost between activate_workspace
    and the next tool call. Keying by the stable user id is the only thing that
    survives. Authenticated users only — anonymous sessions have no stable id.
    """

    __tablename__ = "active_workspaces"

    user_id: str = Field(
        sa_column=Column(Text, ForeignKey("users.id"), primary_key=True, nullable=False)
    )
    workspace: str
    updated_at: str


class WorkspaceMeta(SQLModel, table=True):
    """Extensible per-workspace metadata. The on-disk git repo is the source of
    truth for existence and WorkspaceAccess for access; this row is the source of
    truth for metadata (description, folder, tags, and future fields). Keyed by
    (user_id, workspace), mirroring WorkspaceAccess."""

    __tablename__ = "workspace_meta"

    user_id: str = Field(
        sa_column=Column(Text, ForeignKey("users.id"), primary_key=True, nullable=False)
    )
    workspace: str = Field(primary_key=True)
    description: str = Field(default="", sa_column=Column(Text))
    folder: str = Field(default="")
    tags: str | None = Field(default=None, sa_column=Column(Text))
    updated_at: str


class Note(SQLModel, table=True):
    __tablename__ = "notes"

    id: str = Field(primary_key=True)
    workspace: str
    owner_id: str = Field(default="")
    title: str
    folder: str = Field(default="")
    tags: str | None = Field(default=None, sa_column=Column(Text))
    created_at: str
    updated_at: str
    index_state: str = Field(default="stale")  # 'stale' | 'indexed'
    indexed_at: str | None = None


class NoteLink(SQLModel, table=True):
    """Directed edge in the note link graph: ``source_note_id`` links to ``target_note_id``.

    Deduped to one edge per pair via the composite primary key — whose leading column
    (``source_note_id``) also serves the hot ``WHERE source_note_id = ?`` path (refresh on save,
    delete on note removal) with no extra index. The covering ``(target_note_id, source_note_id)``
    index serves backlinks / move-rewrite as an index-only scan. ``workspace``/``owner_id`` are
    denormalized for bulk cleanup on workspace reset, not for hot-path queries.
    """

    __tablename__ = "note_links"

    source_note_id: str = Field(primary_key=True)
    target_note_id: str = Field(primary_key=True)
    workspace: str
    owner_id: str

    __table_args__ = (Index("ix_note_links_target", "target_note_id", "source_note_id"),)


class Tag(SQLModel, table=True):
    """A node in a workspace's tag hierarchy. ``path`` is the full slash-path
    (bare, lowercased); ancestor rows are materialized so every node exists and
    ``parent_id`` forms a real adjacency-list tree. Structure only for now —
    metadata (color/description) can be added by a later migration.
    """

    __tablename__ = "tags"

    id: str = Field(primary_key=True)
    workspace: str
    owner_id: str
    path: str
    name: str
    parent_id: str | None = Field(default=None, sa_column=Column(Text, ForeignKey("tags.id")))
    created_at: str

    __table_args__ = (
        Index("ix_tags_ws_owner_path", "workspace", "owner_id", "path", unique=True),
        Index("ix_tags_ws_owner_parent", "workspace", "owner_id", "parent_id"),
    )


class NoteTag(SQLModel, table=True):
    """Join row linking a note to a tag it actually carries (never ancestors).

    Deduped to one row per ``(note_id, tag_id)``; ``source`` records where the tag
    came from with frontmatter winning over inline. The ``ix_note_tags_tag`` index
    serves the hot "notes for this tag" lookup.
    """

    __tablename__ = "note_tags"

    note_id: str = Field(
        sa_column=Column(Text, ForeignKey("notes.id"), primary_key=True, nullable=False)
    )
    tag_id: str = Field(
        sa_column=Column(Text, ForeignKey("tags.id"), primary_key=True, nullable=False)
    )
    source: str

    __table_args__ = (Index("ix_note_tags_tag", "tag_id"),)


class OAuthRegisteredClient(SQLModel, table=True):
    __tablename__ = "oauth_registered_clients"

    client_id: str = Field(primary_key=True)
    data: str = Field(sa_column=Column(Text, nullable=False))


class ClientAuthorization(SQLModel, table=True):
    __tablename__ = "client_authorizations"

    client_id: str = Field(primary_key=True)
    user_id: str = Field(sa_column=Column(Text, ForeignKey("users.id"), nullable=False))


class UserSession(SQLModel, table=True):
    __tablename__ = "sessions"

    token: str = Field(primary_key=True)
    user_id: str = Field(sa_column=Column(Text, ForeignKey("users.id"), nullable=False))
    expires_at: int


class OAuthAccessToken(SQLModel, table=True):
    __tablename__ = "oauth_access_tokens"

    token: str = Field(primary_key=True)
    client_id: str
    scopes: str | None = Field(default=None, sa_column=Column(Text))
    expires_at: int | None = None
    refresh_token: str | None = None


class OAuthRefreshToken(SQLModel, table=True):
    __tablename__ = "oauth_refresh_tokens"

    token: str = Field(primary_key=True)
    client_id: str
    scopes: str | None = Field(default=None, sa_column=Column(Text))
    expires_at: int | None = None


class OAuthAuthorizationCode(SQLModel, table=True):
    __tablename__ = "oauth_authorization_codes"

    code: str = Field(primary_key=True)
    client_id: str
    redirect_uri: str = Field(sa_column=Column(Text, nullable=False))
    redirect_uri_provided_explicitly: bool
    scopes: str | None = Field(default=None, sa_column=Column(Text))
    expires_at: float
    code_challenge: str | None = None


class OAuthPendingAuthorization(SQLModel, table=True):
    __tablename__ = "oauth_pending_authorizations"

    pending_id: str = Field(primary_key=True)
    client_json: str = Field(sa_column=Column(Text, nullable=False))
    params_json: str = Field(sa_column=Column(Text, nullable=False))
    expires_at: float


class EmbeddingCache(SQLModel, table=True):
    """Content-addressed cache of chunk embeddings, shared across users on the same
    ``(backend, model)``. Key = sha256 of the exact embedded text + backend + model;
    value is a float32 vector blob. Content-addressed ⇒ immutable, no invalidation."""

    __tablename__ = "embedding_cache"

    content_hash: str = Field(primary_key=True)
    backend: str = Field(primary_key=True)
    model: str = Field(primary_key=True)
    dim: int
    embedding: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    created_at: str
    last_used_at: str


class EmbeddingProfile(SQLModel, table=True):
    """A user-owned embedding backend profile. The active one (``is_active``) drives
    embedding + search. ``dim`` is auto-detected by a probe embed at save; ``api_key_enc``
    is the sealed key (write-only over the API)."""

    __tablename__ = "embedding_profiles"

    id: str = Field(primary_key=True)
    user_id: str = Field(sa_column=Column(Text, ForeignKey("users.id"), nullable=False))
    name: str
    base_url: str
    model: str
    api_key_enc: bytes | None = Field(default=None, sa_column=Column(LargeBinary))
    dim: int
    is_active: bool = Field(default=False)
    created_at: str
    updated_at: str

    __table_args__ = (Index("ix_embedding_profiles_user", "user_id"),)


class NoteChunk(SQLModel, table=True):
    """One structure-aware chunk of a note. ``chunk_rowid`` is the integer key linking
    to the ``note_chunks_vec_{dim}`` row; ``content`` is the raw chunk body (the embedded
    text = breadcrumb + content is recomputed in flight, never stored). ``dim`` records
    which dim-sharded vec table holds this chunk's vector (NULL until embedded)."""

    __tablename__ = "note_chunks"

    chunk_rowid: int | None = Field(default=None, primary_key=True)
    id: str
    note_id: str = Field(sa_column=Column(Text, ForeignKey("notes.id"), nullable=False))
    workspace: str
    owner_id: str
    ordinal: int
    header_path: str  # JSON list, e.g. '["# Title","## Section"]'
    content: str = Field(sa_column=Column(Text))
    char_start: int
    char_end: int
    dim: int | None = None
    created_at: str

    __table_args__ = (Index("ix_note_chunks_note", "note_id"),)


class IndexMeta(SQLModel, table=True):
    """Per-user active embedding-index identity. Drives drift detection / per-user
    reindex on backend switch (the rebuild itself lands in Plan 5)."""

    __tablename__ = "index_meta"

    owner_id: str = Field(primary_key=True)
    backend: str
    model: str
    dim: int
    updated_at: str


class Job(SQLModel, table=True):
    """Durable background job. The DB is the queue: workers claim rows atomically,
    run a handler by ``kind``, and retry with backoff on failure. A partial unique
    index collapses repeated enqueues of the same ``(kind, dedup_key)`` while one is
    still pending (debounce). Timestamps are epoch seconds (float) — the queue does
    time math (backoff, staleness), not human display."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "uq_jobs_pending_dedup",
            "kind",
            "dedup_key",
            unique=True,
            sqlite_where=text("status = 'pending'"),
        ),
        Index("ix_jobs_claim", "status", "next_run_at"),
        Index("ix_jobs_user_id", "user_id"),
    )

    id: str = Field(primary_key=True)
    kind: str = Field(sa_column=Column(Text, nullable=False))
    user_id: str | None = Field(default=None, sa_column=Column(Text, ForeignKey("users.id")))
    dedup_key: str | None = Field(default=None, sa_column=Column(Text))
    payload: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(default="pending", sa_column=Column(Text, nullable=False))
    attempts: int = Field(default=0)
    max_attempts: int = Field(default=5)
    next_run_at: float = Field(default=0.0)
    locked_by: str | None = Field(default=None, sa_column=Column(Text))
    locked_at: float | None = Field(default=None)
    last_error: str | None = Field(default=None, sa_column=Column(Text))
    created_at: float
    updated_at: float
