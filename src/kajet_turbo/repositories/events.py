import json
import time

from nanoid import generate
from sqlalchemy import Engine, text
from sqlmodel import Session, col, select

from kajet_turbo.models import Event


class EventRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(self, owner_id: str, kind: str, payload: dict) -> None:
        event = Event(
            id=generate(size=12),
            owner_id=owner_id,
            kind=kind,
            payload=json.dumps(payload),
            created_at=time.time(),
        )
        with Session(self._engine) as session:
            session.add(event)
            session.commit()

    def claim(self, owner_id: str, kinds: list[str]) -> list[Event]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(Event)
                .where(Event.owner_id == owner_id)
                .where(col(Event.kind).in_(kinds))
                .order_by(col(Event.created_at))
            ).all()
            for row in rows:
                session.delete(row)
            session.commit()
            return list(rows)

    def sweep(self, older_than_s: float) -> int:
        cutoff = time.time() - older_than_s
        with self._engine.connect() as conn:
            result = conn.execute(
                text("DELETE FROM events WHERE created_at < :cutoff"),
                {"cutoff": cutoff},
            )
            conn.commit()
            return result.rowcount
