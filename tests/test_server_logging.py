"""uvicorn's own loggers must flow through the JSON sink, not its default handlers."""

import logging
import sys

import pytest

from tests.helpers import read_log_entries


def test_uvicorn_error_records_reach_the_json_sink(capsys):
    """With no uvicorn log config installed, "uvicorn.error" propagates to the root
    _InterceptHandler and an ASGI exception becomes one structured record."""
    from kajet_turbo.log import setup_logging

    setup_logging()
    try:
        raise TypeError("get_session_repo() missing 1 required positional argument")
    except TypeError:
        # Trailing newline is verbatim uvicorn (protocols/http/httptools_impl.py).
        logging.getLogger("uvicorn.error").error("Exception in ASGI application\n", exc_info=True)

    entry = next(e for e in read_log_entries(capsys) if e["logger"] == "uvicorn.error")
    assert entry["msg"] == "Exception in ASGI application"
    assert entry["logger"] == "uvicorn.error"
    assert entry["level"] == "error"
    assert entry["error_type"] == "TypeError"
    assert "get_session_repo" in entry["error_msg"]


@pytest.mark.parametrize("role", ["api", "mcp", "all"])
def test_main_runs_uvicorn_without_its_default_log_config(monkeypatch, role):
    """uvicorn's default LOGGING_CONFIG attaches a plain-text handler with
    propagate=False to "uvicorn"; log_config=None is what keeps its records in JSONL."""
    import uvicorn

    from kajet_turbo import server

    seen: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: seen.update(kw))
    monkeypatch.setattr(sys, "argv", ["kajet-turbo"])
    monkeypatch.setenv("KAJET_ROLE", role)
    server.main()

    assert "log_config" in seen
    assert seen["log_config"] is None
    # Without an explicit level the bare "uvicorn.error" logger sits at NOTSET, which
    # passes uvicorn's `logger.level <= TRACE` guards and turns on per-message tracing.
    assert seen["log_level"] == "info"
