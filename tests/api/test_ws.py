import time

import pytest
from fastapi import FastAPI
from sqlmodel import Session
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kajet_turbo.api.ws import router
from kajet_turbo.db import Database
from kajet_turbo.dependencies import get_event_repo, get_session_repo
from kajet_turbo.models import User, UserSession
from kajet_turbo.repositories.events import EventRepository
from kajet_turbo.repositories.sessions import SessionRepository


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

    # row consumed — not delivered twice
    assert outbox.claim("u1", ["note_updated"]) == []
