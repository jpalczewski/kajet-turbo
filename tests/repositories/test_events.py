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


# --- observability ------------------------------------------------------------------


def test_publish_emits_db_ms(database: Database):
    from kajet_turbo import perf

    repo = EventRepository(database.engine)
    with perf.perf_span() as span:
        repo.publish("u1", "note_updated", {"note_id": "n1"})
    assert "db_ms" in span.fields


def test_claim_emits_db_ms(database: Database):
    from kajet_turbo import perf

    repo = EventRepository(database.engine)
    with perf.perf_span() as span:
        repo.claim("u1", ["note_updated"])
    assert "db_ms" in span.fields


def test_sweep_emits_db_ms(database: Database):
    from kajet_turbo import perf

    repo = EventRepository(database.engine)
    with perf.perf_span() as span:
        repo.sweep(3600.0)
    assert "db_ms" in span.fields


def test_publish_logs_event(database: Database, capsys):
    from kajet_turbo.log import setup_logging

    setup_logging()
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "n1"})

    entries = [json.loads(ln) for ln in capsys.readouterr().err.strip().split("\n") if ln]
    published = [e for e in entries if e.get("msg") == "event_published"]
    assert len(published) == 1
    assert published[0]["owner_id"] == "u1"
    assert published[0]["kind"] == "note_updated"
    assert published[0]["event_id"]


def test_claim_is_silent_when_there_is_nothing_to_claim(database: Database, capsys):
    """Every open WebSocket polls this every 2s — an empty read must not log at all."""
    from kajet_turbo.log import setup_logging

    setup_logging()
    repo = EventRepository(database.engine)
    repo.claim("u1", ["note_updated"])

    assert "events_claimed" not in capsys.readouterr().err


def test_claim_logs_the_count_when_it_moves_events(database: Database, capsys):
    from kajet_turbo.log import setup_logging

    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "n1"})
    repo.publish("u1", "note_updated", {"note_id": "n2"})

    setup_logging()  # after publish, so only the claim line is captured
    repo.claim("u1", ["note_updated"])

    entries = [json.loads(ln) for ln in capsys.readouterr().err.strip().split("\n") if ln]
    claimed = [e for e in entries if e.get("msg") == "events_claimed"]
    assert len(claimed) == 1
    assert claimed[0]["count"] == 2
    assert claimed[0]["owner_id"] == "u1"
