"""The WS handshake resolves its dependencies against the real application graph.

tests/api/test_ws.py overrides get_session_repo/get_event_repo, so it never exercises
their real signatures. Those dependencies must accept a WebSocket scope: a Request-typed
parameter is left unfilled there and FastAPI calls the provider with no argument (#271
regression, every /api/ws upgrade answered 500 in production).
"""

import time

from sqlmodel import Session
from starlette import status
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kajet_turbo import identity
from kajet_turbo.db import Database
from kajet_turbo.dependencies import AppConfig
from kajet_turbo.models import User, UserSession
from kajet_turbo.server import build_api_app


def _api_app(database: Database, tmp_path):
    return build_api_app(
        AppConfig(
            db_path=database.db_path,
            workspaces_dir=str(tmp_path / "workspaces"),
            mcp_base_url="http://localhost",
            serve_spa=False,
        )
    )


def test_ws_handshake_resolves_real_dependencies_and_accepts_a_session(database, tmp_path):
    with Session(database.engine) as s:
        s.add(User(id="u1", email="u1@example.com", created_at="2026-01-01"))
        s.add(UserSession(token="good-token", user_id="u1", expires_at=int(time.time()) + 86400))
        s.commit()
    app = _api_app(database, tmp_path)
    with TestClient(app) as client:
        client.cookies.set(identity.SESSION_COOKIE, "good-token")
        with client.websocket_connect("/api/ws"):
            pass  # accept() succeeded: the real get_session_repo/get_event_repo resolved


def test_ws_handshake_rejects_missing_session_with_policy_violation(database, tmp_path):
    app = _api_app(database, tmp_path)
    with TestClient(app) as client:
        try:
            with client.websocket_connect("/api/ws"):
                raise AssertionError("handshake should have been refused")
        except WebSocketDisconnect as e:
            assert e.code == status.WS_1008_POLICY_VIOLATION
