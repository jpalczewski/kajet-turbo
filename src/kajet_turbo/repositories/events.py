import json
import time

from nanoid import generate
from sqlalchemy import CursorResult, text
from sqlmodel import col, select

from kajet_turbo.log import logger
from kajet_turbo.models import Event
from kajet_turbo.repositories import DbRepository


class EventRepository(DbRepository):
    def publish(self, owner_id: str, kind: str, payload: dict) -> None:
        # Held as a local: commit() expires the instance and the session closes with the
        # block, so reading event.id afterwards would trigger a refresh on a detached
        # object rather than hand back the id we just generated.
        event_id = generate(size=12)
        event = Event(
            id=event_id,
            owner_id=owner_id,
            kind=kind,
            payload=json.dumps(payload),
            created_at=time.time(),
        )
        with self.timed_session() as session:
            session.add(event)
            session.commit()
        logger.info("event_published", owner_id=owner_id, kind=kind, event_id=event_id)

    def claim(self, owner_id: str, kinds: list[str]) -> list[Event]:
        with self.timed_session() as session:
            rows = session.exec(
                select(Event)
                .where(Event.owner_id == owner_id)
                .where(col(Event.kind).in_(kinds))
                .order_by(col(Event.created_at))
            ).all()
            for row in rows:
                session.delete(row)
            session.commit()
            claimed = list(rows)
        # Every open WebSocket calls this on a 2s poll, so an unconditional line would be
        # pure volume — the failure mode #36 documents for outbox_sweep. Silence on an
        # empty read keeps "an event moved" the only thing this logger ever says.
        if claimed:
            logger.info("events_claimed", owner_id=owner_id, count=len(claimed))
        return claimed

    def sweep(self, older_than_s: float) -> int:
        cutoff = time.time() - older_than_s
        with self.timed_session() as session:
            result = session.execute(  # ty: ignore[deprecated] - DELETE statement
                text("DELETE FROM events WHERE created_at < :cutoff"),
                {"cutoff": cutoff},
            )
            session.commit()
            assert isinstance(result, CursorResult)
            # No log here on purpose: the only caller already reports the count as
            # `outbox_sweep(swept=...)` in server.py, gated on it being non-zero.
            return result.rowcount
