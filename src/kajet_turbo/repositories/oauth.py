import json
import time

from sqlalchemy import Engine, text
from sqlmodel import Session, select

from kajet_turbo.models import ClientAuthorization, OAuthPendingAuthorization, OAuthRegisteredClient


class OAuthRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def upsert_registered_client(self, client_id: str, data: str) -> None:
        with Session(self._engine) as session:
            existing = session.exec(
                select(OAuthRegisteredClient).where(OAuthRegisteredClient.client_id == client_id)
            ).first()
            if existing:
                existing.data = data
                session.add(existing)
            else:
                session.add(OAuthRegisteredClient(client_id=client_id, data=data))
            session.commit()

    def get_all_registered_clients(self) -> list[str]:
        with Session(self._engine) as session:
            rows = session.exec(select(OAuthRegisteredClient)).all()
        return [r.data for r in rows]

    def record_client_authorization(self, client_id: str, user_id: str) -> None:
        with Session(self._engine) as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO client_authorizations (client_id, user_id)"
                    " VALUES (:client_id, :user_id)"
                ),
                {"client_id": client_id, "user_id": user_id},
            )
            session.commit()

    def get_user_id_by_client(self, client_id: str) -> str | None:
        with Session(self._engine) as session:
            row = session.exec(
                select(ClientAuthorization).where(ClientAuthorization.client_id == client_id)
            ).first()
        return row.user_id if row else None

    def upsert_access_token(
        self,
        token: str,
        client_id: str,
        scopes: list[str] | None,
        expires_at: int | None,
        refresh_token: str | None = None,
    ) -> None:
        with Session(self._engine) as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO oauth_access_tokens"
                    " (token, client_id, scopes, expires_at, refresh_token)"
                    " VALUES (:token, :client_id, :scopes, :expires_at, :refresh_token)"
                ),
                {
                    "token": token,
                    "client_id": client_id,
                    "scopes": json.dumps(scopes or []),
                    "expires_at": expires_at,
                    "refresh_token": refresh_token,
                },
            )
            session.commit()

    def upsert_refresh_token(
        self,
        token: str,
        client_id: str,
        scopes: list[str] | None,
        expires_at: int | None,
    ) -> None:
        with Session(self._engine) as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO oauth_refresh_tokens"
                    " (token, client_id, scopes, expires_at)"
                    " VALUES (:token, :client_id, :scopes, :expires_at)"
                ),
                {
                    "token": token,
                    "client_id": client_id,
                    "scopes": json.dumps(scopes or []),
                    "expires_at": expires_at,
                },
            )
            session.commit()

    def get_valid_access_tokens(self) -> list[dict]:
        with Session(self._engine) as session:
            rows = session.execute(
                text(
                    "SELECT token, client_id, scopes, expires_at, refresh_token"
                    " FROM oauth_access_tokens"
                    " WHERE expires_at IS NULL OR expires_at > :now"
                ),
                {"now": int(time.time())},
            ).fetchall()
        return [{**dict(r._mapping), "scopes": json.loads(r.scopes or "[]")} for r in rows]

    def get_valid_refresh_tokens(self) -> list[dict]:
        with Session(self._engine) as session:
            rows = session.execute(
                text(
                    "SELECT token, client_id, scopes, expires_at FROM oauth_refresh_tokens"
                    " WHERE expires_at IS NULL OR expires_at > :now"
                ),
                {"now": int(time.time())},
            ).fetchall()
        return [{**dict(r._mapping), "scopes": json.loads(r.scopes or "[]")} for r in rows]

    def save_oauth_client(
        self,
        client_id: str,
        client_secret: str,
        redirect_uris: list[str],
        created_at: str,
    ) -> None:
        with Session(self._engine) as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO oauth_clients"
                    " (client_id, client_secret, redirect_uris, created_at)"
                    " VALUES (:client_id, :client_secret, :redirect_uris, :created_at)"
                ),
                {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uris": json.dumps(redirect_uris),
                    "created_at": created_at,
                },
            )
            session.commit()

    def get_oauth_client(self, client_id: str) -> dict | None:
        with Session(self._engine) as session:
            row = session.execute(
                text(
                    "SELECT client_id, client_secret, redirect_uris"
                    " FROM oauth_clients WHERE client_id = :client_id"
                ),
                {"client_id": client_id},
            ).fetchone()
        if row is None:
            return None
        return {**dict(row._mapping), "redirect_uris": json.loads(row.redirect_uris)}

    def upsert_pending(self, pending_id: str, client_json: str, params_json: str, expires_at: float) -> None:
        with Session(self._engine) as session:
            session.execute(
                text(
                    "INSERT OR REPLACE INTO oauth_pending_authorizations"
                    " (pending_id, client_json, params_json, expires_at)"
                    " VALUES (:pending_id, :client_json, :params_json, :expires_at)"
                ),
                {"pending_id": pending_id, "client_json": client_json,
                 "params_json": params_json, "expires_at": expires_at},
            )
            session.commit()

    def get_pending(self, pending_id: str) -> tuple[str, str] | None:
        with Session(self._engine) as session:
            row = session.exec(
                select(OAuthPendingAuthorization).where(
                    OAuthPendingAuthorization.pending_id == pending_id,
                    OAuthPendingAuthorization.expires_at > time.time(),
                )
            ).first()
        return (row.client_json, row.params_json) if row else None

    def delete_pending(self, pending_id: str) -> None:
        with Session(self._engine) as session:
            session.execute(
                text("DELETE FROM oauth_pending_authorizations WHERE pending_id = :id"),
                {"id": pending_id},
            )
            session.commit()

    def get_valid_pending(self) -> list[dict]:
        with Session(self._engine) as session:
            rows = session.execute(
                text(
                    "SELECT pending_id, client_json, params_json FROM oauth_pending_authorizations"
                    " WHERE expires_at > :now"
                ),
                {"now": time.time()},
            ).fetchall()
        return [dict(r._mapping) for r in rows]
