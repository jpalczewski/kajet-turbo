from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Text
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


class OAuthPendingAuthorization(SQLModel, table=True):
    __tablename__ = "oauth_pending_authorizations"

    pending_id: str = Field(primary_key=True)
    client_json: str = Field(sa_column=Column(Text, nullable=False))
    params_json: str = Field(sa_column=Column(Text, nullable=False))
    expires_at: float
