from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Index, Text
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
    fts_rowid: int | None = None


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
