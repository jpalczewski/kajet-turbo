"""Repository for per-user SSH keys. All reads are owner-scoped. The unique
``(user_id, name)`` constraint is enforced by the DB; ``create`` translates the
violation into ``DuplicateKeyName`` so the API can answer 409."""

from datetime import UTC, datetime

from nanoid import generate
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from kajet_turbo.models import SshKey


class DuplicateKeyName(Exception):
    """A key with the requested name already exists for this user."""


class SshKeyRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def list_for_user(self, user_id: str) -> list[SshKey]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(SshKey).where(SshKey.user_id == user_id).order_by(SshKey.created_at)
                )
            )

    def get(self, user_id: str, key_id: str) -> SshKey | None:
        with Session(self._engine) as session:
            key = session.get(SshKey, key_id)
            return key if key and key.user_id == user_id else None

    def create(
        self,
        user_id: str,
        name: str,
        algorithm: str,
        public_key: str,
        private_key_enc: bytes,
        fingerprint: str,
    ) -> SshKey:
        now = datetime.now(UTC).isoformat()
        with Session(self._engine) as session:
            key = SshKey(
                id=generate(size=12),
                user_id=user_id,
                name=name,
                algorithm=algorithm,
                public_key=public_key,
                private_key_enc=private_key_enc,
                fingerprint=fingerprint,
                created_at=now,
            )
            session.add(key)
            try:
                session.commit()
            except IntegrityError as e:
                session.rollback()
                raise DuplicateKeyName(name) from e
            session.refresh(key)
            return key

    def delete(self, user_id: str, key_id: str) -> bool:
        with Session(self._engine) as session:
            key = session.get(SshKey, key_id)
            if key is None or key.user_id != user_id:
                return False
            session.delete(key)
            session.commit()
            return True
