import secrets
import time

from sqlalchemy import text

from kajet_turbo.models import UserSession
from kajet_turbo.repositories import DbRepository


class SessionRepository(DbRepository):
    repository_name = "sessions"

    def create(self, user_id: str) -> str:
        token = secrets.token_hex(32)
        expires_at = int(time.time()) + 30 * 24 * 3600
        sess = UserSession(token=token, user_id=user_id, expires_at=expires_at)
        with self.operation("create", user_id=user_id, expires_at=expires_at) as operation:
            session = operation.session
            session.add(sess)
            session.commit()
        return token

    def get_user(self, token: str) -> dict | None:
        # Timed but not logged: this runs on every authenticated REST request, so a line
        # per call would drown the log while telling nobody anything. The db_ms it feeds
        # into the request's span is the part that was missing.
        with self.timed_session() as session:
            row = session.execute(  # ty: ignore[deprecated] - raw SQL
                text(
                    "SELECT u.id, u.email FROM sessions s"
                    " JOIN users u ON u.id = s.user_id"
                    " WHERE s.token = :token AND s.expires_at > :now"
                ),
                {"token": token, "now": int(time.time())},
            ).fetchone()
        return dict(row._mapping) if row else None

    def delete(self, token: str) -> None:
        with self.timed_session() as session:
            session.execute(  # ty: ignore[deprecated] - raw SQL
                text("DELETE FROM sessions WHERE token = :token"), {"token": token}
            )
            session.commit()

    def delete_all_for_user(self, user_id: str) -> int:
        with self.operation("delete_all_for_user", user_id=user_id) as operation:
            session = operation.session
            result = session.execute(  # ty: ignore[deprecated] - raw SQL
                text("DELETE FROM sessions WHERE user_id = :user_id"), {"user_id": user_id}
            )
            session.commit()
            count = result.rowcount  # ty: ignore[unresolved-attribute] - CursorResult at runtime
            operation.add_fields(count=count)
        return count
