import json
import time
from collections.abc import Collection
from dataclasses import dataclass

from nanoid import generate
from sqlalchemy import CursorResult, text
from sqlmodel import col, select

from kajet_turbo.models import Event
from kajet_turbo.repositories import DbRepository


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """A read-out event, detached from the session on purpose.

    Reads return plain values rather than ORM instances: callers use these after the
    session has closed, and a detached SQLModel row raises on attribute access the moment
    anything expires it. Narrow enough that the three fields a consumer needs — cursor
    position, identity for de-duplication, and the body — are the whole type.
    """

    id: str
    created_at: float
    payload: str


class EventRepository(DbRepository):
    repository_name = "events"

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
        with self.operation(
            "publish", owner_id=owner_id, kind=kind, event_id=event_id
        ) as operation:
            session = operation.session
            session.add(event)
            session.commit()

    def read_since(
        self,
        owner_id: str,
        kinds: list[str],
        since: float,
        exclude_ids: Collection[str] = (),
    ) -> list[OutboxEvent]:
        """Events at or after ``since``, oldest first, excluding ``exclude_ids``.

        Non-destructive: rows stay for every other reader and are removed only by
        ``sweep``. That is what lets a second tab see the same event.

        The bound is ``>=``, not ``>``, because there is no monotonic cursor on this
        table — ``created_at`` is a non-unique float and ``id`` is a random nanoid, so an
        exclusive bound would silently drop an event published in the same clock tick as
        the last one delivered. ``exclude_ids`` carries the ids already handled at exactly
        ``since``, which is what keeps the overlap from re-delivering them.
        """
        with self.operation("read_since", owner_id=owner_id) as operation:
            session = operation.session
            rows = session.exec(
                select(Event)
                .where(Event.owner_id == owner_id)
                .where(col(Event.kind).in_(kinds))
                .where(col(Event.created_at) >= since)
                .order_by(col(Event.created_at))
            ).all()
            events = [
                OutboxEvent(id=r.id, created_at=r.created_at, payload=r.payload)
                for r in rows
                if r.id not in exclude_ids
            ]
            # This is polled every 2s per open connection, so an unconditional line would be
            # pure volume — the failure mode #36 documents for outbox_sweep. Staying silent on
            # an empty read keeps "an event moved" the only thing this logger ever says.
            if events:
                operation.outcome = "read"
                operation.add_fields(count=len(events))
            else:
                operation.suppress_log()
        return events

    def sweep(self, older_than_s: float) -> int:
        cutoff = time.time() - older_than_s
        with self.operation("sweep") as operation:
            session = operation.session
            result = session.execute(  # ty: ignore[deprecated] - DELETE statement
                text("DELETE FROM events WHERE created_at < :cutoff"),
                {"cutoff": cutoff},
            )
            session.commit()
            assert isinstance(result, CursorResult)
            # No log here on purpose: the only caller already reports the count as
            # `outbox_sweep(swept=...)` in server.py, gated on it being non-zero.
            operation.suppress_log()
            return result.rowcount
