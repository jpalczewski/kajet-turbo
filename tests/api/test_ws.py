import time

import pytest
from fastapi import FastAPI
from sqlmodel import Session
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kajet_turbo.api import ws
from kajet_turbo.api.ws import router
from kajet_turbo.db import Database
from kajet_turbo.dependencies import get_event_repo, get_session_repo
from kajet_turbo.models import Event, User, UserSession
from kajet_turbo.repositories.events import EventRepository
from kajet_turbo.repositories.sessions import SessionRepository
from tests.helpers import entries_named, read_log_entries


@pytest.fixture(autouse=True)
def _fast_intervals(monkeypatch):
    """The real 2s poll / 30s revalidation cadence would make this file take minutes.
    Every test gets a fast poll and near-immediate revalidation; tests that care about
    "stays open across several cycles while valid" push the revalidation deadline out
    instead of relying on the default."""
    monkeypatch.setattr(ws, "_POLL_INTERVAL_S", 0.02)
    monkeypatch.setattr(ws, "_REVALIDATE_INTERVAL_S", 0.05)


def _make_app(database: Database, user_id: str | None) -> FastAPI:
    """Build a minimal FastAPI app with the WS router and overridden deps."""
    outbox = EventRepository(database.engine)
    test_session_repo = SessionRepository(database.engine)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_event_repo] = lambda: outbox
    app.dependency_overrides[get_session_repo] = lambda: test_session_repo

    if user_id is not None:
        with Session(database.engine) as s:
            if not s.get(User, user_id):
                s.add(User(id=user_id, email=f"{user_id}@t.com", created_at="2026-01-01"))
            s.add(
                UserSession(
                    token="good-token",
                    user_id=user_id,
                    expires_at=int(time.time()) + 86400,
                )
            )
            s.commit()

    return app


def test_ws_rejects_unauthenticated(database: Database):
    app = _make_app(database, user_id=None)
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/api/ws") as ws,
    ):
        # 1008 Policy Violation: Starlette raises WebSocketDisconnect on __enter__.
        ws.receive_text()


def test_ws_delivers_outbox_event(database: Database):
    app = _make_app(database, user_id="u1")
    outbox = EventRepository(database.engine)
    outbox.publish(
        "u1",
        "note_updated",
        {
            "type": "note_updated",
            "owner_id": "u1",
            "workspace": "ws1",
            "note_id": "nid1",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )

    with (
        TestClient(app) as client,
        client.websocket_connect("/api/ws", cookies={"kajet_session": "good-token"}) as ws,
    ):
        msg = ws.receive_json()
    assert msg["type"] == "note_updated"
    assert msg["note_id"] == "nid1"

    # The row survives the read: this is what lets any other connection see it too.
    # Under the old delete-on-read claim() it was gone at this point.
    assert len(outbox.read_since("u1", ["note_updated"], 0.0)) == 1


def test_ws_logs_a_connect_disconnect_pair_sharing_one_conn_id(database: Database, capsys):
    """#39 needed the number of distinct connections and had to infer it from timestamp
    clustering. conn_id makes that a grep."""
    from kajet_turbo.log import setup_logging

    app = _make_app(database, user_id="u1")
    setup_logging()

    with (
        TestClient(app) as client,
        client.websocket_connect("/api/ws", cookies={"kajet_session": "good-token"}),
    ):
        pass

    entries = read_log_entries(capsys)
    connected = entries_named(entries, "ws_connected")
    disconnected = entries_named(entries, "ws_disconnected")

    assert len(connected) == 1
    assert len(disconnected) == 1
    assert connected[0]["user_id"] == "u1"
    assert connected[0]["conn_id"]
    assert disconnected[0]["conn_id"] == connected[0]["conn_id"]
    assert disconnected[0]["duration_s"] >= 0


def test_ws_gives_each_connection_its_own_conn_id(database: Database, capsys):
    from kajet_turbo.log import setup_logging

    app = _make_app(database, user_id="u1")
    setup_logging()

    with TestClient(app) as client:
        for _ in range(2):
            with client.websocket_connect("/api/ws", cookies={"kajet_session": "good-token"}):
                pass

    conn_ids = {e["conn_id"] for e in entries_named(read_log_entries(capsys), "ws_connected")}
    assert len(conn_ids) == 2


def test_two_connections_both_receive_the_same_event(database: Database):
    """The point of #41: delete-on-read meant whichever connection polled first ate the
    event and every other tab kept stale data."""
    app = _make_app(database, user_id="u1")
    EventRepository(database.engine).publish(
        "u1",
        "note_updated",
        {
            "type": "note_updated",
            "owner_id": "u1",
            "workspace": "ws1",
            "note_id": "nid1",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )

    with (
        TestClient(app) as client,
        client.websocket_connect("/api/ws", cookies={"kajet_session": "good-token"}) as a,
        client.websocket_connect("/api/ws", cookies={"kajet_session": "good-token"}) as b,
    ):
        first = a.receive_json()
        second = b.receive_json()

    assert first["note_id"] == "nid1"
    assert second["note_id"] == "nid1"


def test_ws_stays_open_while_session_valid_across_revalidations(database: Database):
    """#79: periodic re-validation must not disconnect a session that is still good."""
    app = _make_app(database, user_id="u1")
    outbox = EventRepository(database.engine)
    outbox.publish(
        "u1",
        "note_updated",
        {
            "type": "note_updated",
            "owner_id": "u1",
            "workspace": "ws1",
            "note_id": "before",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )

    with (
        TestClient(app) as client,
        client.websocket_connect("/api/ws", cookies={"kajet_session": "good-token"}) as sock,
    ):
        assert sock.receive_json()["note_id"] == "before"

        # Several revalidation windows pass while the session in the DB is still valid.
        time.sleep(ws._REVALIDATE_INTERVAL_S * 6)

        outbox.publish(
            "u1",
            "note_updated",
            {
                "type": "note_updated",
                "owner_id": "u1",
                "workspace": "ws1",
                "note_id": "after",
                "updated_at": "2026-01-01T00:00:01+00:00",
            },
        )
        assert sock.receive_json()["note_id"] == "after"


def test_ws_closes_when_session_deleted_server_side(database: Database, capsys):
    """#79: logout (session row gone) must close an already-open socket, not just
    reject the next REST call."""
    from kajet_turbo.log import setup_logging

    app = _make_app(database, user_id="u1")
    setup_logging()
    session_repo = SessionRepository(database.engine)

    with (
        TestClient(app) as client,
        client.websocket_connect("/api/ws", cookies={"kajet_session": "good-token"}) as sock,
    ):
        session_repo.delete("good-token")
        with pytest.raises(WebSocketDisconnect) as exc_info:
            sock.receive_text()

    assert exc_info.value.code == 1008

    revoked = entries_named(read_log_entries(capsys), "ws_session_revoked")
    assert len(revoked) == 1
    assert revoked[0]["user_id"] == "u1"


def test_ws_skips_malformed_event_payload_instead_of_wedging_the_cursor(database: Database, capsys):
    """A bad payload (schema drift, corruption) must not crash the connection nor block
    every event after it forever - the watermark has to advance past it."""
    from kajet_turbo.log import setup_logging

    app = _make_app(database, user_id="u1")
    setup_logging()
    outbox = EventRepository(database.engine)

    with Session(database.engine) as s:
        s.add(
            Event(
                id="bad1",
                owner_id="u1",
                kind="note_updated",
                payload="{not json",
                created_at=1.0,
            )
        )
        s.commit()
    outbox.publish(
        "u1",
        "note_updated",
        {
            "type": "note_updated",
            "owner_id": "u1",
            "workspace": "ws1",
            "note_id": "good",
            "updated_at": "2026-01-01T00:00:01+00:00",
        },
    )

    with (
        TestClient(app) as client,
        client.websocket_connect("/api/ws", cookies={"kajet_session": "good-token"}) as sock,
    ):
        assert sock.receive_json()["note_id"] == "good"

    bad_payload = entries_named(read_log_entries(capsys), "ws_bad_event_payload")
    assert len(bad_payload) == 1
    assert bad_payload[0]["event_id"] == "bad1"
