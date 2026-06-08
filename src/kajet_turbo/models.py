from __future__ import annotations

from typing import Optional

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
    password_hash: Optional[str] = None
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
    title: str
    tags: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: str
    updated_at: str
    fts_rowid: Optional[int] = None


class OAuthRegisteredClient(SQLModel, table=True):
    __tablename__ = "oauth_registered_clients"

    client_id: str = Field(primary_key=True)
    data: str = Field(sa_column=Column(Text, nullable=False))


class ClientAuthorization(SQLModel, table=True):
    __tablename__ = "client_authorizations"

    client_id: str = Field(primary_key=True)
    user_id: str = Field(
        sa_column=Column(Text, ForeignKey("users.id"), nullable=False)
    )


class UserSession(SQLModel, table=True):
    __tablename__ = "sessions"

    token: str = Field(primary_key=True)
    user_id: str = Field(
        sa_column=Column(Text, ForeignKey("users.id"), nullable=False)
    )
    expires_at: int


class OAuthAccessToken(SQLModel, table=True):
    __tablename__ = "oauth_access_tokens"

    token: str = Field(primary_key=True)
    client_id: str
    scopes: Optional[str] = Field(default=None, sa_column=Column(Text))
    expires_at: Optional[int] = None
    refresh_token: Optional[str] = None


class OAuthRefreshToken(SQLModel, table=True):
    __tablename__ = "oauth_refresh_tokens"

    token: str = Field(primary_key=True)
    client_id: str
    scopes: Optional[str] = Field(default=None, sa_column=Column(Text))
    expires_at: Optional[int] = None
