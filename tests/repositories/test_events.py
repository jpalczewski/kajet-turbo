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


def test_read_since_returns_events_without_consuming_them(database: Database):
    """Non-destructive by design — a second reader (another tab) must see the same rows."""
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "n1"})
    repo.publish("u1", "note_updated", {"note_id": "n2"})

    first = repo.read_since("u1", ["note_updated"], 0.0)
    assert len(first) == 2
    assert {json.loads(r.payload)["note_id"] for r in first} == {"n1", "n2"}

    second = repo.read_since("u1", ["note_updated"], 0.0)
    assert [r.id for r in second] == [r.id for r in first]


def test_read_since_filters_by_owner(database: Database):
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "n1"})
    repo.publish("u2", "note_updated", {"note_id": "n2"})

    mine = repo.read_since("u1", ["note_updated"], 0.0)
    assert len(mine) == 1
    assert json.loads(mine[0].payload)["note_id"] == "n1"
    assert repo.read_since("u2", ["note_updated"], 0.0) != []


def test_read_since_filters_by_kind(database: Database):
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "n1"})
    repo.publish("u1", "other_event", {"x": 1})

    updates = repo.read_since("u1", ["note_updated"], 0.0)
    assert len(updates) == 1
    assert json.loads(updates[0].payload)["note_id"] == "n1"

    others = repo.read_since("u1", ["other_event"], 0.0)
    assert len(others) == 1


def test_sweep_deletes_old_rows(database: Database):
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "old"})

    deleted = repo.sweep(older_than_s=0.0)
    assert deleted == 1
    assert repo.read_since("u1", ["note_updated"], 0.0) == []


def test_sweep_keeps_recent_rows(database: Database):
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "recent"})

    deleted = repo.sweep(older_than_s=3600.0)
    assert deleted == 0
    assert len(repo.read_since("u1", ["note_updated"], 0.0)) == 1


# --- observability ------------------------------------------------------------------


def test_publish_emits_db_ms(database: Database):
    from kajet_turbo import perf

    repo = EventRepository(database.engine)
    with perf.perf_span() as span:
        repo.publish("u1", "note_updated", {"note_id": "n1"})
    assert "db_ms" in span.fields


def test_read_since_emits_db_ms(database: Database):
    from kajet_turbo import perf

    repo = EventRepository(database.engine)
    with perf.perf_span() as span:
        repo.read_since("u1", ["note_updated"], 0.0)
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


def test_read_since_is_silent_when_there_is_nothing_to_read(database: Database, capsys):
    """Every open WebSocket polls this every 2s — an empty read must not log at all."""
    from kajet_turbo.log import setup_logging

    setup_logging()
    repo = EventRepository(database.engine)
    repo.read_since("u1", ["note_updated"], 0.0)

    assert "events_read" not in capsys.readouterr().err


def test_read_since_logs_the_count_when_it_returns_events(database: Database, capsys):
    from kajet_turbo.log import setup_logging

    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "n1"})
    repo.publish("u1", "note_updated", {"note_id": "n2"})

    setup_logging()  # after publish, so only the read line is captured
    repo.read_since("u1", ["note_updated"], 0.0)

    entries = [json.loads(ln) for ln in capsys.readouterr().err.strip().split("\n") if ln]
    read = [e for e in entries if e.get("msg") == "events_read"]
    assert len(read) == 1
    assert read[0]["count"] == 2
    assert read[0]["owner_id"] == "u1"


# --- cursor semantics ---------------------------------------------------------------


def test_read_since_bound_is_inclusive(database: Database):
    """`created_at` is a non-unique float and `id` a random nanoid, so there is no
    monotonic cursor. An exclusive bound would drop an event sharing a tick with the last
    one delivered; the inclusive bound plus exclude_ids is what makes the overlap safe."""
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "n1"})

    only = repo.read_since("u1", ["note_updated"], 0.0)[0]
    assert repo.read_since("u1", ["note_updated"], only.created_at) == [only]


def test_read_since_excludes_ids_already_handled(database: Database):
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "n1"})

    only = repo.read_since("u1", ["note_updated"], 0.0)[0]
    assert repo.read_since("u1", ["note_updated"], only.created_at, {only.id}) == []


def test_read_since_skips_events_older_than_the_cursor(database: Database):
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "old"})
    older = repo.read_since("u1", ["note_updated"], 0.0)[0]

    repo.publish("u1", "note_updated", {"note_id": "new"})
    fresh = repo.read_since("u1", ["note_updated"], older.created_at, {older.id})

    assert [json.loads(r.payload)["note_id"] for r in fresh] == ["new"]


def test_read_since_returns_detached_safe_values(database: Database):
    """Callers read these after the session closed — the ORM row would raise there."""
    repo = EventRepository(database.engine)
    repo.publish("u1", "note_updated", {"note_id": "n1"})

    event = repo.read_since("u1", ["note_updated"], 0.0)[0]
    assert isinstance(event.id, str)
    assert isinstance(event.created_at, float)
    assert json.loads(event.payload)["note_id"] == "n1"
