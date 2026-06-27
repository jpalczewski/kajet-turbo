import json

from kajet_turbo.db import Database
from kajet_turbo.repositories.events import EventRepository


def test_publish_inserts_row(database: Database):
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "abc"})

    from sqlmodel import Session, select

    from kajet_turbo.models import Event

    with Session(database.engine) as s:
        rows = s.exec(select(Event)).all()
    assert len(rows) == 1
    assert rows[0].owner_id == "u1"
    assert rows[0].kind == "note_updated"
    assert json.loads(rows[0].payload) == {"note_id": "abc"}


def test_claim_returns_and_deletes(database: Database):
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "n1"})
    repo.publish("u1", "note_updated", {"note_id": "n2"})

    claimed = repo.claim("u1", ["note_updated"])
    assert len(claimed) == 2
    assert {json.loads(r.payload)["note_id"] for r in claimed} == {"n1", "n2"}

    claimed_again = repo.claim("u1", ["note_updated"])
    assert claimed_again == []


def test_claim_filters_by_owner(database: Database):
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "n1"})
    repo.publish("u2", "note_updated", {"note_id": "n2"})

    claimed = repo.claim("u1", ["note_updated"])
    assert len(claimed) == 1
    assert json.loads(claimed[0].payload)["note_id"] == "n1"
    assert repo.claim("u2", ["note_updated"]) != []


def test_claim_filters_by_kind(database: Database):
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "n1"})
    repo.publish("u1", "other_event", {"x": 1})

    claimed = repo.claim("u1", ["note_updated"])
    assert len(claimed) == 1
    assert claimed[0].kind == "note_updated"

    remaining = repo.claim("u1", ["other_event"])
    assert len(remaining) == 1


def test_sweep_deletes_old_rows(database: Database):
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "old"})

    deleted = repo.sweep(older_than_s=0.0)
    assert deleted == 1
    assert repo.claim("u1", ["note_updated"]) == []


def test_sweep_keeps_recent_rows(database: Database):
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "recent"})

    deleted = repo.sweep(older_than_s=3600.0)
    assert deleted == 0
    assert len(repo.claim("u1", ["note_updated"])) == 1
