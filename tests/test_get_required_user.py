from unittest.mock import patch

import pytest
from fastapi import HTTPException

from kajet_turbo.dependencies import CurrentUser, get_required_user
from kajet_turbo.errors import SecurityEvent, SecurityReason
from kajet_turbo.log import setup_logging
from tests.helpers import entries_named, read_log_entries


def test_get_required_user_raises_401_when_no_session(tmp_path, monkeypatch, capsys):
    with patch("kajet_turbo.dependencies.get_session_user", return_value=None):
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "client": ("203.0.113.5", 12345),
            "headers": [
                (b"cookie", b"kajet_session=private-session-cookie"),
                (b"user-agent", b"pytest-client/1.0"),
            ],
            "query_string": b"",
        }
        request = Request(scope)
        setup_logging()
        with pytest.raises(HTTPException) as exc_info:
            get_required_user(request)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "NOT_AUTHENTICATED"
    (event,) = entries_named(read_log_entries(capsys), SecurityEvent.AUTH_FAILURE.value)
    assert event["reason"] == SecurityReason.NO_SESSION.value
    assert event["auth_method"] == "session_cookie"
    assert event["client_ip"] == "203.0.113.5"
    assert event["user_agent"] == "pytest-client/1.0"
    assert "private-session-cookie" not in str(event)


def test_get_required_user_returns_user_when_session_exists():
    user = {"id": "u1", "email": "u@test.com", "timezone": "UTC", "locale": "en"}
    with patch("kajet_turbo.dependencies.get_session_user", return_value=user):
        from starlette.requests import Request

        scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
        request = Request(scope)
        result = get_required_user(request)
        assert result == CurrentUser(id="u1", email="u@test.com", timezone="UTC", locale="en")
