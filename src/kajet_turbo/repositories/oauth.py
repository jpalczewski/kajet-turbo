import json
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlmodel import Session, select

from kajet_turbo.log import logger
from kajet_turbo.models import OAuthPendingAuthorization, OAuthRegisteredClient
from kajet_turbo.repositories import DbRepository


@dataclass(frozen=True)
class OAuthTokenPair:
    access_token: str
    refresh_token: str
    client_id: str
    user_id: str
    scopes: list[str]
    access_expires_at: int
    refresh_expires_at: int
    family_id: str


class RotationOutcome(StrEnum):
    ROTATED = "rotated"
    REUSED = "reused"
    EXPIRED = "expired"
    MISSING = "missing"


def _row_dict(row, *json_fields: str) -> dict[str, Any]:
    value = dict(row._mapping)
    for field in json_fields:
        value[field] = json.loads(value.get(field) or "[]")
    return value


class OAuthRepository(DbRepository):
    """Persistent OAuth state with timing and secret-safe structured logging."""

    @staticmethod
    def _log(operation: str, *, outcome: str = "success", **context: object) -> None:
        logger.info("oauth_repository", operation=operation, outcome=outcome, **context)

    @staticmethod
    def _insert_access_token(session: Session, pair: OAuthTokenPair) -> None:
        session.execute(  # ty: ignore[deprecated] - raw SQL
            text(
                "INSERT INTO oauth_access_tokens"
                " (token, client_id, user_id, scopes, expires_at, refresh_token)"
                " VALUES (:token, :client_id, :user_id, :scopes, :expires_at, :refresh_token)"
            ),
            {
                "token": pair.access_token,
                "client_id": pair.client_id,
                "user_id": pair.user_id,
                "scopes": json.dumps(pair.scopes),
                "expires_at": pair.access_expires_at,
                "refresh_token": pair.refresh_token,
            },
        )

    @staticmethod
    def _insert_refresh_token(session: Session, pair: OAuthTokenPair) -> None:
        session.execute(  # ty: ignore[deprecated] - raw SQL
            text(
                "INSERT INTO oauth_refresh_tokens"
                " (token, client_id, user_id, scopes, expires_at, family_id, consumed_at)"
                " VALUES (:token, :client_id, :user_id, :scopes, :expires_at, :family_id, NULL)"
            ),
            {
                "token": pair.refresh_token,
                "client_id": pair.client_id,
                "user_id": pair.user_id,
                "scopes": json.dumps(pair.scopes),
                "expires_at": pair.refresh_expires_at,
                "family_id": pair.family_id,
            },
        )

    @staticmethod
    def _delete_family(session: Session, family_id: str) -> tuple[int, int]:
        access = session.execute(  # ty: ignore[deprecated] - raw SQL
            text(
                "DELETE FROM oauth_access_tokens WHERE refresh_token IN"
                " (SELECT token FROM oauth_refresh_tokens WHERE family_id = :family_id)"
            ),
            {"family_id": family_id},
        )
        refresh = session.execute(  # ty: ignore[deprecated] - raw SQL
            text("DELETE FROM oauth_refresh_tokens WHERE family_id = :family_id"),
            {"family_id": family_id},
        )
        return (
            access.rowcount,  # ty: ignore[unresolved-attribute] - CursorResult at runtime
            refresh.rowcount,  # ty: ignore[unresolved-attribute] - CursorResult at runtime
        )

    def upsert_registered_client(self, client_id: str, data: str) -> None:
        with self.timed_session() as session:
            existing = session.exec(
                select(OAuthRegisteredClient).where(OAuthRegisteredClient.client_id == client_id)
            ).first()
            if existing:
                existing.data = data
                session.add(existing)
            else:
                session.add(OAuthRegisteredClient(client_id=client_id, data=data))
            session.commit()
        self._log("upsert_registered_client", client_id=client_id)

    def get_all_registered_clients(self) -> list[str]:
        with self.timed_session() as session:
            rows = session.exec(select(OAuthRegisteredClient)).all()
        self._log("get_all_registered_clients", count=len(rows))
        return [row.data for row in rows]

    def get_registered_client(self, client_id: str) -> str | None:
        with self.timed_session() as session:
            row = session.exec(
                select(OAuthRegisteredClient).where(OAuthRegisteredClient.client_id == client_id)
            ).first()
        self._log("get_registered_client", client_id=client_id, found=row is not None)
        return row.data if row else None

    def record_client_authorization(self, client_id: str, user_id: str) -> None:
        with self.timed_session() as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "INSERT OR REPLACE INTO client_authorizations (client_id, user_id)"
                    " VALUES (:client_id, :user_id)"
                ),
                {"client_id": client_id, "user_id": user_id},
            )
            session.commit()
        self._log("record_client_authorization", client_id=client_id, user_id=user_id)

    def upsert_access_token(
        self,
        token: str,
        client_id: str,
        scopes: list[str] | None,
        expires_at: int | None,
        refresh_token: str | None = None,
        user_id: str | None = None,
    ) -> None:
        with self.timed_session() as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "INSERT OR REPLACE INTO oauth_access_tokens"
                    " (token, client_id, user_id, scopes, expires_at, refresh_token)"
                    " VALUES (:token, :client_id, :user_id, :scopes, :expires_at, :refresh_token)"
                ),
                {
                    "token": token,
                    "client_id": client_id,
                    "user_id": user_id,
                    "scopes": json.dumps(scopes or []),
                    "expires_at": expires_at,
                    "refresh_token": refresh_token,
                },
            )
            session.commit()
        self._log("upsert_access_token", client_id=client_id, user_id=user_id)

    def upsert_refresh_token(
        self,
        token: str,
        client_id: str,
        scopes: list[str] | None,
        expires_at: int | None,
        user_id: str | None = None,
        *,
        family_id: str | None = None,
        consumed_at: int | None = None,
    ) -> None:
        with self.timed_session() as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "INSERT OR REPLACE INTO oauth_refresh_tokens"
                    " (token, client_id, user_id, scopes, expires_at, family_id, consumed_at)"
                    " VALUES (:token, :client_id, :user_id, :scopes, :expires_at,"
                    " :family_id, :consumed_at)"
                ),
                {
                    "token": token,
                    "client_id": client_id,
                    "user_id": user_id,
                    "scopes": json.dumps(scopes or []),
                    "expires_at": expires_at,
                    "family_id": family_id or token,
                    "consumed_at": consumed_at,
                },
            )
            session.commit()
        self._log("upsert_refresh_token", client_id=client_id, user_id=user_id)

    def save_token_pair(self, pair: OAuthTokenPair) -> None:
        with self.timed_session() as session:
            self._insert_refresh_token(session, pair)
            self._insert_access_token(session, pair)
            session.commit()
        self._log(
            "save_token_pair",
            client_id=pair.client_id,
            user_id=pair.user_id,
            family_id=pair.family_id,
        )

    def rotate_token_pair(
        self, old_refresh_token: str, pair: OAuthTokenPair, *, now: int
    ) -> RotationOutcome:
        """Consume the old RT and persist its successor in one transaction."""
        with self.timed_session() as session:
            claimed = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "UPDATE oauth_refresh_tokens SET consumed_at = :now"
                    " WHERE token = :token AND consumed_at IS NULL"
                    " AND (expires_at IS NULL OR expires_at >= :now) RETURNING family_id"
                ),
                {"token": old_refresh_token, "now": now},
            ).fetchone()
            if claimed is not None:
                if claimed.family_id != pair.family_id:
                    raise ValueError("Replacement refresh token has a different family_id")
                session.execute(  # ty: ignore[deprecated] - raw SQL
                    text("DELETE FROM oauth_access_tokens WHERE refresh_token = :token"),
                    {"token": old_refresh_token},
                )
                self._insert_refresh_token(session, pair)
                self._insert_access_token(session, pair)
                session.commit()
                outcome = RotationOutcome.ROTATED
            else:
                row = session.execute(  # ty: ignore[deprecated] - raw SQL
                    text(
                        "SELECT family_id, consumed_at, expires_at"
                        " FROM oauth_refresh_tokens WHERE token = :token"
                    ),
                    {"token": old_refresh_token},
                ).fetchone()
                if row is None:
                    outcome = RotationOutcome.MISSING
                else:
                    self._delete_family(session, row.family_id)
                    outcome = (
                        RotationOutcome.REUSED
                        if row.consumed_at is not None
                        else RotationOutcome.EXPIRED
                    )
                session.commit()
        self._log(
            "rotate_token_pair",
            outcome=outcome,
            client_id=pair.client_id,
            user_id=pair.user_id,
            family_id=pair.family_id,
        )
        return outcome

    def get_valid_access_tokens(self) -> list[dict[str, Any]]:
        with self.timed_session() as session:
            rows = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "SELECT token, client_id, user_id, scopes, expires_at, refresh_token"
                    " FROM oauth_access_tokens WHERE expires_at IS NULL OR expires_at > :now"
                ),
                {"now": int(time.time())},
            ).fetchall()
        self._log("get_valid_access_tokens", count=len(rows))
        return [_row_dict(row, "scopes") for row in rows]

    def get_valid_refresh_tokens(self) -> list[dict[str, Any]]:
        with self.timed_session() as session:
            rows = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "SELECT token, client_id, user_id, scopes, expires_at, family_id, consumed_at"
                    " FROM oauth_refresh_tokens WHERE consumed_at IS NULL"
                    " AND (expires_at IS NULL OR expires_at > :now)"
                ),
                {"now": int(time.time())},
            ).fetchall()
        self._log("get_valid_refresh_tokens", count=len(rows))
        return [_row_dict(row, "scopes") for row in rows]

    def get_access_token(self, token: str) -> dict[str, Any] | None:
        with self.timed_session() as session:
            row = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "SELECT token, client_id, user_id, scopes, expires_at, refresh_token"
                    " FROM oauth_access_tokens WHERE token = :token"
                ),
                {"token": token},
            ).fetchone()
        self._log("get_access_token", found=row is not None)
        return _row_dict(row, "scopes") if row is not None else None

    def get_refresh_token(self, token: str) -> dict[str, Any] | None:
        with self.timed_session() as session:
            row = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "SELECT token, client_id, user_id, scopes, expires_at, family_id, consumed_at"
                    " FROM oauth_refresh_tokens WHERE token = :token"
                ),
                {"token": token},
            ).fetchone()
        self._log("get_refresh_token", found=row is not None)
        return _row_dict(row, "scopes") if row is not None else None

    def upsert_auth_code(
        self,
        code: str,
        client_id: str,
        user_id: str | None,
        redirect_uri: str,
        redirect_uri_provided_explicitly: bool,
        scopes: list[str] | None,
        expires_at: float,
        code_challenge: str | None,
    ) -> None:
        with self.timed_session() as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "INSERT OR REPLACE INTO oauth_authorization_codes"
                    " (code, client_id, user_id, redirect_uri, redirect_uri_provided_explicitly,"
                    " scopes, expires_at, code_challenge)"
                    " VALUES (:code, :client_id, :user_id, :redirect_uri, :explicit,"
                    " :scopes, :expires_at, :code_challenge)"
                ),
                {
                    "code": code,
                    "client_id": client_id,
                    "user_id": user_id,
                    "redirect_uri": redirect_uri,
                    "explicit": redirect_uri_provided_explicitly,
                    "scopes": json.dumps(scopes or []),
                    "expires_at": expires_at,
                    "code_challenge": code_challenge,
                },
            )
            session.commit()
        self._log("upsert_auth_code", client_id=client_id, user_id=user_id)

    def get_auth_code(self, code: str) -> dict[str, Any] | None:
        with self.timed_session() as session:
            row = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "SELECT code, client_id, user_id, redirect_uri,"
                    " redirect_uri_provided_explicitly, scopes, expires_at, code_challenge"
                    " FROM oauth_authorization_codes WHERE code = :code"
                ),
                {"code": code},
            ).fetchone()
        self._log("get_auth_code", found=row is not None)
        return _row_dict(row, "scopes") if row is not None else None

    def delete_auth_code(self, code: str) -> bool:
        with self.timed_session() as session:
            result = session.execute(  # ty: ignore[deprecated] - raw SQL
                text("DELETE FROM oauth_authorization_codes WHERE code = :code"), {"code": code}
            )
            session.commit()
        deleted = result.rowcount > 0  # ty: ignore[unresolved-attribute] - CursorResult
        self._log("delete_auth_code", deleted=deleted)
        return deleted

    def save_oauth_client(
        self, client_id: str, client_secret: str, redirect_uris: list[str], created_at: str
    ) -> None:
        with self.timed_session() as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
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
        self._log("save_oauth_client", client_id=client_id)

    def get_oauth_client(self, client_id: str) -> dict[str, Any] | None:
        with self.timed_session() as session:
            row = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "SELECT client_id, client_secret, redirect_uris"
                    " FROM oauth_clients WHERE client_id = :client_id"
                ),
                {"client_id": client_id},
            ).fetchone()
        self._log("get_oauth_client", client_id=client_id, found=row is not None)
        return _row_dict(row, "redirect_uris") if row is not None else None

    def upsert_pending(
        self, pending_id: str, client_json: str, params_json: str, expires_at: float
    ) -> None:
        with self.timed_session() as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "INSERT OR REPLACE INTO oauth_pending_authorizations"
                    " (pending_id, client_json, params_json, expires_at)"
                    " VALUES (:pending_id, :client_json, :params_json, :expires_at)"
                ),
                {
                    "pending_id": pending_id,
                    "client_json": client_json,
                    "params_json": params_json,
                    "expires_at": expires_at,
                },
            )
            session.commit()
        self._log("upsert_pending")

    def get_pending(self, pending_id: str) -> tuple[str, str] | None:
        with self.timed_session() as session:
            row = session.exec(
                select(OAuthPendingAuthorization).where(
                    OAuthPendingAuthorization.pending_id == pending_id,
                    OAuthPendingAuthorization.expires_at > time.time(),
                )
            ).first()
        self._log("get_pending", found=row is not None)
        return (row.client_json, row.params_json) if row else None

    def delete_pending(self, pending_id: str) -> None:
        with self.timed_session() as session:
            result = session.execute(  # ty: ignore[deprecated] - raw SQL
                text("DELETE FROM oauth_pending_authorizations WHERE pending_id = :id"),
                {"id": pending_id},
            )
            session.commit()
        self._log(
            "delete_pending",
            deleted=result.rowcount,  # ty: ignore[unresolved-attribute] - CursorResult
        )

    def delete_access_token(self, token: str) -> None:
        with self.timed_session() as session:
            result = session.execute(  # ty: ignore[deprecated] - raw SQL
                text("DELETE FROM oauth_access_tokens WHERE token = :token"), {"token": token}
            )
            session.commit()
        self._log(
            "delete_access_token",
            deleted=result.rowcount,  # ty: ignore[unresolved-attribute] - CursorResult
        )

    def delete_refresh_token(self, token: str) -> bool:
        with self.timed_session() as session:
            result = session.execute(  # ty: ignore[deprecated] - raw SQL
                text("DELETE FROM oauth_refresh_tokens WHERE token = :token"), {"token": token}
            )
            session.commit()
        deleted = result.rowcount > 0  # ty: ignore[unresolved-attribute] - CursorResult
        self._log("delete_refresh_token", deleted=deleted)
        return deleted

    def delete_access_tokens_by_refresh(self, refresh_token: str) -> None:
        with self.timed_session() as session:
            result = session.execute(  # ty: ignore[deprecated] - raw SQL
                text("DELETE FROM oauth_access_tokens WHERE refresh_token = :token"),
                {"token": refresh_token},
            )
            session.commit()
        self._log(
            "delete_access_tokens_by_refresh",
            deleted=result.rowcount,  # ty: ignore[unresolved-attribute] - CursorResult
        )

    def revoke_token_family(self, family_id: str) -> bool:
        with self.timed_session() as session:
            access_count, refresh_count = self._delete_family(session, family_id)
            session.commit()
        self._log(
            "revoke_token_family",
            family_id=family_id,
            access_count=access_count,
            refresh_count=refresh_count,
        )
        return bool(access_count or refresh_count)

    def revoke_by_refresh_token(self, token: str) -> bool:
        with self.timed_session() as session:
            row = session.execute(  # ty: ignore[deprecated] - raw SQL
                text("SELECT family_id FROM oauth_refresh_tokens WHERE token = :token"),
                {"token": token},
            ).fetchone()
            if row is None:
                access_count = refresh_count = 0
            else:
                access_count, refresh_count = self._delete_family(session, row.family_id)
            session.commit()
        revoked = bool(access_count or refresh_count)
        self._log(
            "revoke_by_refresh_token",
            revoked=revoked,
            access_count=access_count,
            refresh_count=refresh_count,
        )
        return revoked

    def revoke_by_access_token(self, token: str) -> bool:
        with self.timed_session() as session:
            row = session.execute(  # ty: ignore[deprecated] - raw SQL
                text("SELECT refresh_token FROM oauth_access_tokens WHERE token = :token"),
                {"token": token},
            ).fetchone()
            if row is None:
                access_count = refresh_count = 0
            elif row.refresh_token is None:
                result = session.execute(  # ty: ignore[deprecated] - raw SQL
                    text("DELETE FROM oauth_access_tokens WHERE token = :token"), {"token": token}
                )
                access_count, refresh_count = (
                    result.rowcount,  # ty: ignore[unresolved-attribute] - CursorResult
                    0,
                )
            else:
                refresh = session.execute(  # ty: ignore[deprecated] - raw SQL
                    text("SELECT family_id FROM oauth_refresh_tokens WHERE token = :token"),
                    {"token": row.refresh_token},
                ).fetchone()
                if refresh is None:
                    result = session.execute(  # ty: ignore[deprecated] - raw SQL
                        text("DELETE FROM oauth_access_tokens WHERE token = :token"),
                        {"token": token},
                    )
                    access_count, refresh_count = (
                        result.rowcount,  # ty: ignore[unresolved-attribute] - CursorResult
                        0,
                    )
                else:
                    access_count, refresh_count = self._delete_family(session, refresh.family_id)
            session.commit()
        revoked = bool(access_count or refresh_count)
        self._log(
            "revoke_by_access_token",
            revoked=revoked,
            access_count=access_count,
            refresh_count=refresh_count,
        )
        return revoked

    def delete_credentials_by_user(self, user_id: str) -> int:
        with self.timed_session() as session:
            counts = []
            for table in (
                "oauth_authorization_codes",
                "oauth_access_tokens",
                "oauth_refresh_tokens",
            ):
                result = session.execute(  # ty: ignore[deprecated] - fixed table names
                    text(f"DELETE FROM {table} WHERE user_id = :user_id"), {"user_id": user_id}
                )
                counts.append(
                    result.rowcount  # ty: ignore[unresolved-attribute] - CursorResult
                )
            session.commit()
        deleted = sum(counts)
        self._log("delete_credentials_by_user", user_id=user_id, deleted=deleted)
        return deleted

    def delete_expired_tokens(self) -> None:
        now = int(time.time())
        with self.timed_session() as session:
            deleted = 0
            for statement in (
                "DELETE FROM oauth_access_tokens"
                " WHERE expires_at IS NOT NULL AND expires_at <= :now",
                "DELETE FROM oauth_refresh_tokens"
                " WHERE expires_at IS NOT NULL AND expires_at <= :now",
                "DELETE FROM oauth_authorization_codes WHERE expires_at <= :now",
                "DELETE FROM oauth_pending_authorizations WHERE expires_at <= :now",
            ):
                result = session.execute(text(statement), {"now": now})  # ty: ignore[deprecated]
                deleted += result.rowcount  # ty: ignore[unresolved-attribute] - CursorResult
            session.commit()
        self._log("delete_expired_tokens", deleted=deleted)
