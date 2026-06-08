import secrets
import time

from sqlalchemy import Engine, text
from sqlmodel import Session

from kajet_turbo.models import UserSession


class SessionRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def create(self, user_id: str) -> str:
        token = secrets.token_hex(32)
        expires_at = int(time.time()) + 30 * 24 * 3600
        sess = UserSession(token=token, user_id=user_id, expires_at=expires_at)
        with Session(self._engine) as session:
            session.add(sess)
            session.commit()
        return token

    def get_user(self, token: str) -> dict | None:
        with Session(self._engine) as session:
            row = session.execute(
                text(
                    "SELECT u.id, u.email FROM sessions s"
                    " JOIN users u ON u.id = s.user_id"
                    " WHERE s.token = :token AND s.expires_at > :now"
                ),
                {"token": token, "now": int(time.time())},
            ).fetchone()
        return dict(row._mapping) if row else None

    def delete(self, token: str) -> None:
        with Session(self._engine) as session:
            session.execute(text("DELETE FROM sessions WHERE token = :token"), {"token": token})
            session.commit()
