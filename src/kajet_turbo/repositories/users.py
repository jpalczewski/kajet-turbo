from datetime import UTC, datetime

from nanoid import generate
from sqlalchemy import text
from sqlmodel import select

from kajet_turbo.models import User
from kajet_turbo.repositories import DbRepository


class UserRepository(DbRepository):
    repository_name = "users"

    def create(self, email: str, password_hash: str) -> str:
        user_id = generate(size=12)
        now = datetime.now(UTC).isoformat()
        user = User(id=user_id, email=email, password_hash=password_hash, created_at=now)
        with self.operation("create", user_id=user_id) as operation:
            session = operation.session
            session.add(user)
            session.commit()
        return user_id

    def get_by_email(self, email: str) -> User | None:
        with self.timed_session() as session:
            return session.exec(select(User).where(User.email == email)).first()

    def get(self, user_id: str) -> User | None:
        with self.timed_session() as session:
            return session.get(User, user_id)

    def update_preferences(
        self, user_id: str, *, timezone: str | None = None, locale: str | None = None
    ) -> User | None:
        with self.operation(
            "update_preferences", user_id=user_id, timezone=timezone, locale=locale
        ) as operation:
            session = operation.session
            user = session.get(User, user_id)
            if user is None:
                operation.suppress_log()
                return None
            if timezone is not None:
                user.timezone = timezone
            if locale is not None:
                user.locale = locale
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

    def count(self) -> int:
        with self.timed_session() as session:
            result = session.execute(  # ty: ignore[deprecated] - raw SQL
                text("SELECT COUNT(*) FROM users")
            )
            return result.scalar() or 0
