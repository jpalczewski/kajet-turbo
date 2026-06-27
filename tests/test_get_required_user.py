from unittest.mock import patch

import pytest
from fastapi import HTTPException

from kajet_turbo.dependencies import get_required_user


def test_get_required_user_raises_401_when_no_session(tmp_path, monkeypatch):
    with patch("kajet_turbo.dependencies.get_session_user", return_value=None):
        from starlette.requests import Request

        scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
        request = Request(scope)
        with pytest.raises(HTTPException) as exc_info:
            get_required_user(request)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "NOT_AUTHENTICATED"


def test_get_required_user_returns_user_when_session_exists():
    user = {"id": "u1", "email": "u@test.com"}
    with patch("kajet_turbo.dependencies.get_session_user", return_value=user):
        from starlette.requests import Request

        scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
        request = Request(scope)
        result = get_required_user(request)
        assert result == user
