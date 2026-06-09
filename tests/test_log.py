import json

import pytest


def test_json_sink_produces_valid_jsonl(capsys):
    from kajet_turbo.log import logger, setup_logging

    setup_logging()
    logger.info("hello world", foo="bar")

    captured = capsys.readouterr()
    lines = [l for l in captured.err.strip().split("\n") if l]
    entry = json.loads(lines[-1])
    assert entry["msg"] == "hello world"
    assert entry["level"] == "info"
    assert entry["foo"] == "bar"
    assert "ts" in entry


def test_json_sink_includes_exception_fields(capsys):
    from kajet_turbo.log import logger, setup_logging

    setup_logging()
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("something failed")

    captured = capsys.readouterr()
    lines = [l for l in captured.err.strip().split("\n") if l]
    entry = json.loads(lines[-1])
    assert entry["error_type"] == "ValueError"
    assert entry["error_msg"] == "boom"


async def test_logged_tool_logs_on_success(capsys):
    from kajet_turbo.log import logged_tool, setup_logging

    setup_logging()

    @logged_tool
    async def my_tool() -> str:
        return "ok"

    result = await my_tool()
    assert result == "ok"

    captured = capsys.readouterr()
    lines = [l for l in captured.err.strip().split("\n") if l]
    entry = json.loads(lines[-1])
    assert entry["msg"] == "my_tool"
    assert entry["tool"] == "my_tool"
    assert "duration_ms" in entry


async def test_logged_tool_propagates_exception(capsys):
    from kajet_turbo.log import logged_tool, setup_logging

    setup_logging()

    @logged_tool
    async def broken_tool() -> str:
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError, match="fail"):
        await broken_tool()

    captured = capsys.readouterr()
    lines = [l for l in captured.err.strip().split("\n") if l]
    entry = json.loads(lines[-1])
    assert entry["level"] == "error"
    assert entry["error_type"] == "RuntimeError"


def test_logging_middleware_logs_http_entry(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from kajet_turbo.log import LoggingMiddleware, setup_logging

    setup_logging()
    app = FastAPI()
    app.add_middleware(LoggingMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    with TestClient(app) as client:
        client.get("/ping")

    captured = capsys.readouterr()
    lines = [l for l in captured.err.strip().split("\n") if l]
    entries = [json.loads(l) for l in lines]
    http_entries = [e for e in entries if e.get("msg") == "http"]
    assert len(http_entries) == 1
    e = http_entries[0]
    assert e["method"] == "GET"
    assert e["path"] == "/ping"
    assert e["status"] == 200
    assert "duration_ms" in e


def test_logging_middleware_injects_request_id(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    from fastapi import FastAPI
    from loguru import logger
    from starlette.testclient import TestClient

    from kajet_turbo.log import LoggingMiddleware, setup_logging

    setup_logging()
    app = FastAPI()
    app.add_middleware(LoggingMiddleware)

    @app.get("/ctx")
    def ctx_route():
        logger.info("inside request")
        return {}

    with TestClient(app) as client:
        client.get("/ctx")

    captured = capsys.readouterr()
    lines = [l for l in captured.err.strip().split("\n") if l]
    entries = [json.loads(l) for l in lines]
    inside = next(e for e in entries if e.get("msg") == "inside request")
    assert "request_id" in inside
