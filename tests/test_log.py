import json
import threading
import time

import pytest

from tests.helpers import entries_named, make_logging_app, read_log_entries


def test_json_sink_produces_valid_jsonl(capsys):
    from kajet_turbo.log import logger, setup_logging

    setup_logging()
    logger.info("hello world", foo="bar")

    entry = read_log_entries(capsys)[-1]
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

    entry = read_log_entries(capsys)[-1]
    assert entry["error_type"] == "ValueError"
    assert entry["error_msg"] == "boom"


def _log_dedup_boom(logger, *, level="ERROR", note_id=None) -> None:
    """Single call site: real duplicate FastMCP lines share one source line (the same
    call_tool() re-entered per mount level), so the dedup key must too — a test that
    calls logger.error() from two different lines would get two different `origin`s
    and could never observe a collision either way."""
    if note_id is None:
        logger.log(level, "dedup_boom")
    else:
        logger.log(level, "dedup_boom", note_id=note_id)


def test_json_sink_dedupes_identical_error_lines_within_one_request(capsys):
    from kajet_turbo.log import logger, setup_logging

    setup_logging()
    with logger.contextualize(request_id="r1"):
        _log_dedup_boom(logger)
        _log_dedup_boom(logger)

    assert len(entries_named(read_log_entries(capsys), "dedup_boom")) == 1


def test_json_sink_keeps_lines_that_differ_only_in_extra_fields(capsys):
    from kajet_turbo.log import logger, setup_logging

    setup_logging()
    with logger.contextualize(request_id="r1"):
        _log_dedup_boom(logger, note_id="a")
        _log_dedup_boom(logger, note_id="b")

    entries = entries_named(read_log_entries(capsys), "dedup_boom")
    assert sorted(e["note_id"] for e in entries) == ["a", "b"]


def test_json_sink_does_not_dedupe_different_request_ids(capsys):
    from kajet_turbo.log import logger, setup_logging

    setup_logging()
    with logger.contextualize(request_id="r1"):
        _log_dedup_boom(logger)
    with logger.contextualize(request_id="r2"):
        _log_dedup_boom(logger)

    assert len(entries_named(read_log_entries(capsys), "dedup_boom")) == 2


def test_json_sink_does_not_dedupe_without_request_id(capsys):
    from kajet_turbo.log import logger, setup_logging

    setup_logging()
    _log_dedup_boom(logger)
    _log_dedup_boom(logger)

    assert len(entries_named(read_log_entries(capsys), "dedup_boom")) == 2


def test_json_sink_does_not_dedupe_info_level(capsys):
    from kajet_turbo.log import logger, setup_logging

    setup_logging()
    with logger.contextualize(request_id="r1"):
        _log_dedup_boom(logger, level="INFO")
        _log_dedup_boom(logger, level="INFO")

    assert len(entries_named(read_log_entries(capsys), "dedup_boom")) == 2


def test_json_sink_dedup_respects_kajet_cache_off(capsys, monkeypatch):
    monkeypatch.setenv("KAJET_CACHE", "0")
    from kajet_turbo.log import logger, setup_logging

    setup_logging()
    with logger.contextualize(request_id="r1"):
        _log_dedup_boom(logger)
        _log_dedup_boom(logger)

    assert len(entries_named(read_log_entries(capsys), "dedup_boom")) == 2


async def test_logged_tool_logs_on_success(capsys):
    from kajet_turbo.log import logged_tool, setup_logging

    setup_logging()

    @logged_tool
    async def my_tool() -> str:
        return "ok"

    result = await my_tool()
    assert result == "ok"

    entry = read_log_entries(capsys)[-1]
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

    entry = read_log_entries(capsys)[-1]
    assert entry["level"] == "error"
    assert entry["error_type"] == "RuntimeError"


def test_logging_middleware_logs_http_entry(capsys):
    from starlette.testclient import TestClient

    app = make_logging_app()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    with TestClient(app) as client:
        client.get("/ping")

    http_entries = entries_named(read_log_entries(capsys), "http")
    assert len(http_entries) == 1
    e = http_entries[0]
    assert e["method"] == "GET"
    assert e["path"] == "/ping"
    assert e["status"] == 200
    assert e["user_id"] is None
    assert "duration_ms" in e


def test_logging_middleware_injects_request_id(capsys):
    from loguru import logger
    from starlette.testclient import TestClient

    app = make_logging_app()

    @app.get("/ctx")
    def ctx_route():
        logger.info("inside request")
        return {}

    with TestClient(app) as client:
        client.get("/ctx")

    (inside,) = entries_named(read_log_entries(capsys), "inside request")
    assert "request_id" in inside


def test_logging_middleware_skips_successful_health_logs(capsys, monkeypatch):
    from starlette.testclient import TestClient

    from kajet_turbo.log import LoggingMiddleware

    async def fail_resolve_user_id(self, request, request_id):
        raise AssertionError("identity must not be resolved for health checks")

    monkeypatch.setattr(LoggingMiddleware, "_resolve_user_id", fail_resolve_user_id)

    app = make_logging_app()

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert entries_named(read_log_entries(capsys), "http") == []


def test_logging_middleware_logs_failed_health_as_warning(capsys):
    from starlette.testclient import TestClient

    app = make_logging_app()

    @app.get("/readyz")
    def readyz():
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content={"status": "error"})

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    http_entries = entries_named(read_log_entries(capsys), "http")
    assert len(http_entries) == 1
    assert http_entries[0]["level"] == "warning"
    assert http_entries[0]["path"] == "/readyz"
    assert http_entries[0]["status"] == 503


def test_identity_lookup_runs_outside_the_route_perf_span(capsys, monkeypatch):
    """Tagging a log line is not the route's work. While the lookup runs there must be no
    active span, or its limiter_wait_ms and db_ms land on the route's http entry and send
    perf analysis after a route that touched no database."""
    from starlette.testclient import TestClient

    import kajet_turbo.dependencies as dependencies
    from kajet_turbo.perf import current as perf_current

    spans = []

    class SessionRepo:
        def get_user(self, token):
            spans.append(perf_current())
            return {"id": "user-123", "email": "user@example.com"}

    monkeypatch.setattr(dependencies, "session_repo", SessionRepo())
    app = make_logging_app()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    with TestClient(app) as client:
        client.cookies.set("kajet_session", "session-token")
        client.get("/ping")

    assert spans == [None]
    (http_entry,) = entries_named(read_log_entries(capsys), "http")
    assert http_entry["user_id"] == "user-123"


def test_auth_db_time_lands_on_the_route_span(capsys, database):
    """The other half of the ordering: a session lookup done *for the route* must show up
    in the route's db_ms. FastAPI runs the sync dependency in a threadpool, anyio copies
    the context into it, so the repo's timed_session finds the span the middleware opened.
    Before SessionRepository moved onto DbRepository this was silently zero."""
    from fastapi import Depends
    from starlette.requests import Request
    from starlette.testclient import TestClient

    from kajet_turbo import identity
    from kajet_turbo.repositories.sessions import SessionRepository
    from tests.services.conftest import seed_user

    seed_user(database, "u1")
    repo = SessionRepository(database.engine)
    token = repo.create("u1")

    app = make_logging_app()

    def current_user(request: Request) -> dict | None:
        cookie = identity.session_token_from_cookies(request.cookies)
        return identity.resolve_session_user(repo, cookie)

    @app.get("/me")
    def me(user: dict | None = Depends(current_user)):
        return {"id": user["id"] if user else None}

    with TestClient(app) as client:
        client.cookies.set("kajet_session", token)
        response = client.get("/me")

    assert response.json() == {"id": "u1"}
    (http_entry,) = entries_named(read_log_entries(capsys), "http")
    assert "db_ms" in http_entry


def _cookie_request(token: str = "session-token"):
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "headers": [(b"cookie", f"kajet_session={token}".encode())],
            "query_string": b"",
            "server": ("testserver", 80),
        }
    )


async def test_resolve_user_id_uses_run_sync_and_caches_success(monkeypatch):
    import kajet_turbo.dependencies as dependencies
    from kajet_turbo.log import LoggingMiddleware

    main_thread = threading.get_ident()
    calls = 0

    class SessionRepo:
        def get_user(self, token):
            nonlocal calls
            # The lookup blocks on the DB, so it must not run on the event loop.
            assert threading.get_ident() != main_thread
            calls += 1
            assert token == "session-token"
            return {"id": "user-123", "email": "user@example.com"}

    monkeypatch.setattr(dependencies, "session_repo", SessionRepo())
    middleware = LoggingMiddleware(None)
    request = _cookie_request()

    assert await middleware._resolve_user_id(request, "req-1") == "user-123"
    assert await middleware._resolve_user_id(request, "req-2") == "user-123"
    assert calls == 1


async def test_resolve_user_id_caches_a_credential_that_is_nobody(monkeypatch):
    """A stale cookie is sent on every request its browser makes. Without caching the
    failure, each one is a fresh DB roundtrip through the shared limiter."""
    import kajet_turbo.dependencies as dependencies
    from kajet_turbo.log import LoggingMiddleware

    calls = 0

    class SessionRepo:
        def get_user(self, token):
            nonlocal calls
            calls += 1
            return None

    monkeypatch.setattr(dependencies, "session_repo", SessionRepo())
    middleware = LoggingMiddleware(None)
    request = _cookie_request("stale-token")

    assert await middleware._resolve_user_id(request, "req-1") is None
    assert await middleware._resolve_user_id(request, "req-2") is None
    assert calls == 1


async def test_resolve_user_id_does_not_cache_when_kajet_cache_is_off(monkeypatch):
    monkeypatch.setenv("KAJET_CACHE", "0")
    import kajet_turbo.dependencies as dependencies
    from kajet_turbo.log import LoggingMiddleware

    calls = 0

    class SessionRepo:
        def get_user(self, token):
            nonlocal calls
            calls += 1
            return {"id": "user-123", "email": "user@example.com"}

    monkeypatch.setattr(dependencies, "session_repo", SessionRepo())
    middleware = LoggingMiddleware(None)
    request = _cookie_request()

    assert await middleware._resolve_user_id(request, "req-1") == "user-123"
    assert await middleware._resolve_user_id(request, "req-2") == "user-123"
    assert calls == 2


async def test_resolve_user_id_keeps_no_raw_credential_in_the_cache(monkeypatch):
    """Cache keys outlive the request by the whole TTL — a live cookie must not be
    what is sitting in that dict."""
    import kajet_turbo.dependencies as dependencies
    from kajet_turbo.log import LoggingMiddleware

    class SessionRepo:
        def get_user(self, token):
            return {"id": "user-123", "email": "user@example.com"}

    monkeypatch.setattr(dependencies, "session_repo", SessionRepo())
    middleware = LoggingMiddleware(None)

    await middleware._resolve_user_id(_cookie_request(), "req-1")

    assert middleware._user_ids is not None
    keys = list(middleware._user_ids._cache)
    assert keys and all("session-token" not in str(key) for key in keys)


async def test_resolve_user_id_refuses_an_expired_bearer_token(monkeypatch):
    """Logs must agree with auth's verdict: an expired token is rejected there, so it
    must not tag a log line here — and the client lookup must not even be reached."""
    from starlette.requests import Request

    import kajet_turbo.dependencies as dependencies
    from kajet_turbo.log import LoggingMiddleware

    class OAuthRepo:
        def get_access_token(self, token):
            return {
                "client_id": "client-1",
                "user_id": "user-123",
                "expires_at": time.time() - 10,
            }

    monkeypatch.setattr(dependencies, "oauth_repo", OAuthRepo())
    middleware = LoggingMiddleware(None)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "headers": [(b"authorization", b"Bearer at-expired")],
            "query_string": b"",
            "server": ("testserver", 80),
        }
    )

    assert await middleware._resolve_user_id(request, "req-1") is None


def test_logging_middleware_uses_opaque_id_for_session_and_bearer(capsys, monkeypatch):
    from starlette.testclient import TestClient

    import kajet_turbo.dependencies as dependencies

    class SessionRepo:
        def get_user(self, token):
            assert token == "session-token"
            return {"id": "user-123", "email": "user@example.com"}

    class OAuthRepo:
        def get_access_token(self, token):
            assert token == "access-token"
            return {
                "client_id": "client-1",
                "user_id": "user-123",
                "expires_at": int(time.time()) + 3600,
            }

    monkeypatch.setattr(dependencies, "session_repo", SessionRepo())
    monkeypatch.setattr(dependencies, "oauth_repo", OAuthRepo())
    app = make_logging_app()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    with TestClient(app) as client:
        client.cookies.set("kajet_session", "session-token")
        client.get("/ping")
        client.cookies.clear()
        client.get("/ping", headers={"authorization": "Bearer access-token"})

    http_entries = entries_named(read_log_entries(capsys), "http")
    assert [entry["user_id"] for entry in http_entries] == ["user-123", "user-123"]
    assert all("user@example.com" not in json.dumps(entry) for entry in http_entries)


def test_logging_middleware_ignores_identity_lookup_failure(capsys, monkeypatch):
    from starlette.testclient import TestClient

    import kajet_turbo.dependencies as dependencies

    class SessionRepo:
        def get_user(self, token):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(dependencies, "session_repo", SessionRepo())
    app = make_logging_app()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    with TestClient(app) as client:
        client.cookies.set("kajet_session", "session-token")
        response = client.get("/ping")

    assert response.status_code == 200
    entries = read_log_entries(capsys)
    (http_entry,) = entries_named(entries, "http")
    assert http_entry["user_id"] is None
    # Degrading to an untagged line is right; doing it silently is not — a lookup broken
    # for every request would otherwise be indistinguishable from anonymous traffic.
    (failure,) = entries_named(entries, "log_identity_lookup_failed")
    assert failure["level"] == "warning"
    assert failure["error_type"] == "RuntimeError"
    assert failure["credential_kind"] == "session"


# Stand-in for websockets.exceptions.ConnectionClosedError — matched by class name
# in _handle_loop_exception, not isinstance, since websockets is only a transitive
# dependency. Named exactly "ConnectionClosedError" (via type(), not a class
# statement) so the name-based check in the code under test actually matches.
_FakeConnectionClosedError = type("ConnectionClosedError", (Exception,), {})


def test_handle_loop_exception_downgrades_shielded_connection_closed(capsys):
    from kajet_turbo.log import _handle_loop_exception, setup_logging

    setup_logging()
    exc = _FakeConnectionClosedError("sent 1011 (internal error) keepalive ping timeout")
    context = {"message": "ConnectionClosedError exception in shielded future", "exception": exc}
    calls = []
    fake_loop = type(
        "FakeLoop", (), {"default_exception_handler": lambda self, c: calls.append(c)}
    )()

    _handle_loop_exception(fake_loop, context)

    assert calls == []
    entry = read_log_entries(capsys)[-1]
    assert entry["msg"] == "ws_shielded_connection_closed"
    assert entry["level"] == "warning"
    assert entry["error_type"] == "ConnectionClosedError"


def test_handle_loop_exception_delegates_other_exceptions(capsys):
    from kajet_turbo.log import _handle_loop_exception, setup_logging

    setup_logging()
    context = {"message": "exception in shielded future", "exception": ValueError("boom")}
    calls = []
    fake_loop = type(
        "FakeLoop", (), {"default_exception_handler": lambda self, c: calls.append(c)}
    )()

    _handle_loop_exception(fake_loop, context)

    assert calls == [context]
    captured = capsys.readouterr()
    assert captured.err.strip() == ""


def test_handle_loop_exception_delegates_non_shielded_connection_closed(capsys):
    from kajet_turbo.log import _handle_loop_exception, setup_logging

    setup_logging()
    exc = _FakeConnectionClosedError("some other failure, not from asyncio.shield")
    context = {"message": "Task exception was never retrieved", "exception": exc}
    calls = []
    fake_loop = type(
        "FakeLoop", (), {"default_exception_handler": lambda self, c: calls.append(c)}
    )()

    _handle_loop_exception(fake_loop, context)

    assert calls == [context]
    captured = capsys.readouterr()
    assert captured.err.strip() == ""


def test_json_sink_includes_origin_fields(capsys):
    from kajet_turbo.log import logger, setup_logging

    setup_logging()
    logger.info("origin check")

    entry = read_log_entries(capsys)[-1]
    assert entry["logger"] == __name__
    module, function, line = entry["origin"].split(":")
    assert module == __name__.rsplit(".", 1)[-1]
    assert function == "test_json_sink_includes_origin_fields"
    assert int(line) > 0


def test_intercepted_record_keeps_stdlib_logger_name(capsys):
    import logging

    from kajet_turbo.log import setup_logging

    setup_logging()
    # A library logger we do not pin above DEBUG, so the record reaches the sink.
    logging.getLogger("some_library.submodule").warning("library message")

    entry = read_log_entries(capsys)[-1]
    assert entry["msg"] == "library message"
    # The stdlib name must survive interception; loguru's frame-derived name would be
    # this test module, which tells you nothing about which library emitted the line.
    assert entry["logger"] == "some_library.submodule"


@pytest.mark.parametrize("name", ["sse_starlette", "dulwich", "dulwich.config"])
def test_noisy_library_debug_records_are_suppressed(capsys, name):
    import logging

    from kajet_turbo.log import setup_logging

    setup_logging()
    logging.getLogger(name).debug("chunk: b'event: message ... note body'")

    captured = capsys.readouterr()
    assert "note body" not in captured.err


def test_noisy_library_warnings_still_reach_the_sink(capsys):
    import logging

    from kajet_turbo.log import setup_logging

    setup_logging()
    logging.getLogger("sse_starlette").warning("something actually wrong")

    entry = read_log_entries(capsys)[-1]
    assert entry["msg"] == "something actually wrong"
    assert entry["logger"] == "sse_starlette"


def test_our_own_debug_records_still_reach_the_sink(capsys, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    from kajet_turbo.log import logger, setup_logging

    setup_logging()
    logger.debug("our debug line", detail="kept")

    entry = read_log_entries(capsys)[-1]
    assert entry["msg"] == "our debug line"
    assert entry["detail"] == "kept"


def test_origin_field_is_not_clobbered_by_a_bound_src_field(capsys):
    from kajet_turbo.log import logger, setup_logging

    setup_logging()
    # `src` is a real event field elsewhere (folder_moved logs src/dst). Bound fields are
    # spread last, so a collision would silently overwrite the origin of the line.
    logger.info("folder_moved", src="a", dst="b")

    entry = read_log_entries(capsys)[-1]
    assert entry["src"] == "a"
    assert entry["dst"] == "b"
    _, function, _ = entry["origin"].split(":")
    assert function == "test_origin_field_is_not_clobbered_by_a_bound_src_field"
