from dataclasses import dataclass

from nanoid import generate
from sqlalchemy import case
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, col, select, update

from kajet_turbo.models import Notice
from kajet_turbo.repositories import DbRepository


@dataclass(frozen=True, slots=True)
class RaisedNotice:
    id: str
    count: int

    @property
    def opened(self) -> bool:
        """True when this raise started an episode — a new row or a resolved one reopened.

        Reopening resets ``count`` to 1 (see ``raise_in_session``), so ``count == 1`` is
        exactly "the user has not been told about this occurrence yet"."""
        return self.count == 1


@dataclass(frozen=True, slots=True)
class NoticeRow:
    """An active notice, detached from its session (see ``OutboxEvent`` for why)."""

    id: str
    kind: str
    key: str
    payload: str
    count: int
    first_seen_at: float
    last_seen_at: float
    acknowledged_at: float | None


class NoticeNotFoundError(ValueError):
    def __init__(self, notice_id: str):
        super().__init__(f"No active notice found for notice_id={notice_id!r}")
        self.notice_id = notice_id


class NoticeRepository(DbRepository):
    """Keyed standing conditions. The write methods take a caller-owned session because
    every state change commits together with the outbox event announcing it
    (``NoticeService``); only the reads open their own."""

    repository_name = "notices"

    @staticmethod
    def raise_in_session(
        session: Session, user_id: str, kind: str, key: str, payload: str, now: float
    ) -> RaisedNotice:
        """Insert the notice, or record one more occurrence of it.

        On conflict an *active* row is bumped (``count + 1``, ``last_seen_at``) and keeps
        its acknowledgement: a user who dismissed a still-ongoing condition is not nagged
        again by its 201st occurrence. A *resolved* row is reopened as a new episode — count,
        first-seen, acknowledgement and delivery throttle all reset — because a condition
        that came back is news. SQLite evaluates every SET expression against the pre-update
        row, so each ``case`` sees the old ``resolved_at``.
        """
        reopening = col(Notice.resolved_at).is_not(None)
        stmt = (
            sqlite_insert(Notice)
            .values(
                id=generate(size=12),
                user_id=user_id,
                kind=kind,
                key=key,
                payload=payload,
                count=1,
                first_seen_at=now,
                last_seen_at=now,
            )
            .on_conflict_do_update(
                index_elements=[Notice.user_id, Notice.key],
                set_={
                    "kind": kind,
                    "payload": payload,
                    "last_seen_at": now,
                    "resolved_at": None,
                    "count": case((reopening, 1), else_=col(Notice.count) + 1),
                    "first_seen_at": case((reopening, now), else_=col(Notice.first_seen_at)),
                    "acknowledged_at": case((reopening, None), else_=col(Notice.acknowledged_at)),
                    "last_delivered_at": case(
                        (reopening, None), else_=col(Notice.last_delivered_at)
                    ),
                },
            )
            .returning(col(Notice.id), col(Notice.count))
        )
        notice_id, count = session.exec(stmt).one()
        return RaisedNotice(id=notice_id, count=count)

    @staticmethod
    def resolve_in_session(session: Session, user_id: str, key: str, now: float) -> bool:
        """Mark the active notice resolved. False when there was none (already resolved,
        or never raised)."""
        result = session.exec(
            update(Notice)
            .where(col(Notice.user_id) == user_id)
            .where(col(Notice.key) == key)
            .where(col(Notice.resolved_at).is_(None))
            .values(resolved_at=now)
        )
        return result.rowcount == 1

    @staticmethod
    def acknowledge_in_session(session: Session, user_id: str, notice_id: str, now: float) -> bool:
        """Acknowledge an active notice the user owns. Returns True when this call changed
        it, False when it was already acknowledged; raises when there is no such active
        notice for this user — the same error for "not found" and "not yours", so the
        answer does not reveal another user's ids."""
        owned_active = (
            (col(Notice.id) == notice_id)
            & (col(Notice.user_id) == user_id)
            & col(Notice.resolved_at).is_(None)
        )
        result = session.exec(
            update(Notice)
            .where(owned_active)
            .where(col(Notice.acknowledged_at).is_(None))
            .values(acknowledged_at=now)
        )
        if result.rowcount == 1:
            return True
        if session.exec(select(Notice.id).where(owned_active)).first() is None:
            raise NoticeNotFoundError(notice_id)
        return False

    def find_active_id(self, user_id: str, key: str) -> str | None:
        """Cheap indexed probe in front of ``resolve``. Resolving runs after every
        successful embed, and an UPDATE takes SQLite's write lock even when it matches
        nothing — this read keeps the common "nothing to resolve" case off the lock."""
        with self.timed_session() as session:
            return session.exec(
                select(Notice.id)
                .where(Notice.user_id == user_id)
                .where(Notice.key == key)
                .where(col(Notice.resolved_at).is_(None))
            ).first()

    def list_active(self, user_id: str) -> list[NoticeRow]:
        """Unresolved notices, oldest episode first. Acknowledged ones are included with
        ``acknowledged_at`` set; whether to show them is the transport's call."""
        with self.timed_session() as session:
            rows = session.exec(
                select(Notice)
                .where(Notice.user_id == user_id)
                .where(col(Notice.resolved_at).is_(None))
                .order_by(col(Notice.first_seen_at), col(Notice.id))
            ).all()
            return [
                NoticeRow(
                    id=r.id,
                    kind=r.kind,
                    key=r.key,
                    payload=r.payload,
                    count=r.count,
                    first_seen_at=r.first_seen_at,
                    last_seen_at=r.last_seen_at,
                    acknowledged_at=r.acknowledged_at,
                )
                for r in rows
            ]
