from datetime import UTC, datetime

from nanoid import generate
from sqlalchemy import Engine, text
from sqlmodel import Session, select

from kajet_turbo.models import User


class UserRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def create(self, email: str, password_hash: str) -> str:
        user_id = generate(size=12)
        now = datetime.now(UTC).isoformat()
        user = User(id=user_id, email=email, password_hash=password_hash, created_at=now)
        with Session(self._engine) as session:
            session.add(user)
            session.commit()
        return user_id

    def get_by_email(self, email: str) -> dict | None:
        with Session(self._engine) as session:
            user = session.exec(select(User).where(User.email == email)).first()
        if user is None:
            return None
        return {"id": user.id, "email": user.email, "password_hash": user.password_hash}

    def count(self) -> int:
        with Session(self._engine) as session:
            return session.execute(text("SELECT COUNT(*) FROM users")).scalar() or 0
