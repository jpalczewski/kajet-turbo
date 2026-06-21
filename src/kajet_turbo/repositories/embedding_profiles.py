"""Repository for per-user embedding profiles. Exactly one profile per user is active;
``create`` auto-activates the user's first profile, ``set_active`` flips the flag
atomically. All reads are owner-scoped."""

from datetime import UTC, datetime

from nanoid import generate
from sqlalchemy import Engine
from sqlmodel import Session, select

from kajet_turbo.models import EmbeddingProfile


class EmbeddingProfileRepository:
    def __init__(self, engine: Engine):
        self._engine = engine

    def list_for_user(self, user_id: str) -> list[EmbeddingProfile]:
        with Session(self._engine) as session:
            return list(
                session.exec(
                    select(EmbeddingProfile)
                    .where(EmbeddingProfile.user_id == user_id)
                    .order_by(EmbeddingProfile.created_at)
                )
            )

    def get(self, user_id: str, profile_id: str) -> EmbeddingProfile | None:
        with Session(self._engine) as session:
            p = session.get(EmbeddingProfile, profile_id)
            return p if p and p.user_id == user_id else None

    def get_active(self, user_id: str) -> EmbeddingProfile | None:
        with Session(self._engine) as session:
            return session.exec(
                select(EmbeddingProfile).where(
                    EmbeddingProfile.user_id == user_id,
                    EmbeddingProfile.is_active == True,  # noqa: E712 - SQL boolean compare
                )
            ).first()

    def create(self, user_id, name, base_url, model, api_key_enc, dim) -> EmbeddingProfile:
        now = datetime.now(UTC).isoformat()
        with Session(self._engine) as session:
            has_any = session.exec(
                select(EmbeddingProfile).where(EmbeddingProfile.user_id == user_id)
            ).first()
            profile = EmbeddingProfile(
                id=generate(size=12),
                user_id=user_id,
                name=name,
                base_url=base_url,
                model=model,
                api_key_enc=api_key_enc,
                dim=dim,
                is_active=has_any is None,  # first profile auto-activates
                created_at=now,
                updated_at=now,
            )
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return profile

    def update(self, user_id, profile_id, *, name, base_url, model, api_key_enc, dim) -> None:
        now = datetime.now(UTC).isoformat()
        with Session(self._engine) as session:
            p = session.get(EmbeddingProfile, profile_id)
            if p is None or p.user_id != user_id:
                raise ValueError("profile not found")
            p.name, p.base_url, p.model, p.dim = name, base_url, model, dim
            p.api_key_enc = api_key_enc
            p.updated_at = now
            session.add(p)
            session.commit()

    def set_active(self, user_id: str, profile_id: str) -> None:
        with Session(self._engine) as session:
            target = session.get(EmbeddingProfile, profile_id)
            if target is None or target.user_id != user_id:
                raise ValueError("profile not found")
            for p in session.exec(
                select(EmbeddingProfile).where(EmbeddingProfile.user_id == user_id)
            ):
                p.is_active = p.id == profile_id
                session.add(p)
            session.commit()

    def delete(self, user_id: str, profile_id: str) -> None:
        with Session(self._engine) as session:
            p = session.get(EmbeddingProfile, profile_id)
            if p is None or p.user_id != user_id:
                return
            was_active = p.is_active
            session.delete(p)
            session.commit()
            if was_active:
                remaining = session.exec(
                    select(EmbeddingProfile)
                    .where(EmbeddingProfile.user_id == user_id)
                    .order_by(EmbeddingProfile.created_at.desc())  # ty: ignore[unresolved-attribute] - col desc()
                ).first()
                if remaining is not None:
                    remaining.is_active = True
                    session.add(remaining)
                    session.commit()
