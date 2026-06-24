import inspect
import json
import logging
import os
import sys
import time
import uuid
from functools import wraps

from loguru import logger


def _json_sink(message) -> None:
    r = message.record
    entry = {
        "ts": r["time"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "level": r["level"].name.lower(),
        "msg": r["message"],
        **r["extra"],
    }
    if r["exception"]:
        t, v, _ = r["exception"]
        entry["error_type"] = t.__name__ if t else None
        entry["error_msg"] = str(v) if v else None
    print(json.dumps(entry, ensure_ascii=False), file=sys.stderr)


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = inspect.currentframe(), 0
        while frame:
            filename = frame.f_code.co_filename
            is_logging = filename == logging.__file__
            is_frozen = "importlib" in filename and "_bootstrap" in filename
            if depth > 0 and not (is_logging or is_frozen):
                break
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.remove()
    logger.add(_json_sink, level=level)
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    uvicorn_access_level = logging.DEBUG if level == "DEBUG" else logging.WARNING
    logging.getLogger("uvicorn.access").setLevel(uvicorn_access_level)
    sql_level = logging.DEBUG if os.getenv("LOG_SQL") else logging.WARNING
    logging.getLogger("sqlalchemy.engine").setLevel(sql_level)
    # FastMCP uses stdlib logging with propagate=False and its own RichHandler.
    # Replace it with our InterceptHandler so FastMCP logs flow through loguru → JSONL.
    fastmcp_log = logging.getLogger("fastmcp")
    fastmcp_log.handlers.clear()
    fastmcp_log.addHandler(_InterceptHandler())
    fastmcp_log.propagate = False


def _is_framework_param(param: inspect.Parameter) -> bool:
    if hasattr(param.default, "dependency"):  # FastAPI Depends(...)
        return True
    ann = param.annotation
    return hasattr(ann, "__name__") and ann.__name__ == "Request"


def logged_route(fn):
    _skip = frozenset(
        name
        for name, param in inspect.signature(fn).parameters.items()
        if _is_framework_param(param)
    )

    if inspect.iscoroutinefunction(fn):

        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            params = {k: v for k, v in kwargs.items() if k not in _skip}
            result = await fn(*args, **kwargs)
            logger.debug(fn.__name__, **params)
            return result

        return async_wrapper
    else:

        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            params = {k: v for k, v in kwargs.items() if k not in _skip}
            result = fn(*args, **kwargs)
            logger.debug(fn.__name__, **params)
            return result

        return sync_wrapper


# Tools slower than this log at WARNING for easy alerting/profiling. Tune via
# SLOW_TOOL_MS; set 0 to always log tool completions at INFO.
_SLOW_TOOL_MS = float(os.getenv("SLOW_TOOL_MS", "2000"))


def _tool_ctx(args, kwargs):
    """Find the FastMCP Context among a tool's arguments (duck-typed)."""
    for value in (*args, *kwargs.values()):
        if hasattr(value, "session_id") and hasattr(value, "request_id"):
            return value
    return None


def logged_tool(fn):
    @wraps(fn)
    async def wrapper(*args, **kwargs):
        # Bind from the live tool context: the FastMCP session task captures the
        # middleware contextvars at session-init time, so without this, tool logs
        # would carry the initialize request's ids instead of this call's session.
        ctx = _tool_ctx(args, kwargs)
        bind: dict[str, str] = {}
        if ctx is not None:
            for key in ("session_id", "request_id"):
                try:
                    val = getattr(ctx, key)
                except Exception:
                    continue
                if val:
                    bind[key] = val
        start = time.monotonic()
        with logger.contextualize(**bind):
            try:
                result = await fn(*args, **kwargs)
                duration_ms = round((time.monotonic() - start) * 1000)
                level = "warning" if _SLOW_TOOL_MS and duration_ms >= _SLOW_TOOL_MS else "info"
                logger.log(level.upper(), fn.__name__, tool=fn.__name__, duration_ms=duration_ms)
                return result
            except Exception:
                logger.exception(
                    fn.__name__,
                    tool=fn.__name__,
                    duration_ms=round((time.monotonic() - start) * 1000),
                )
                raise

    return wrapper


class LoggingMiddleware:
    """Raw ASGI middleware — safe for SSE/streaming unlike BaseHTTPMiddleware."""

    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        from starlette.requests import Request

        request = Request(scope)
        request_id = str(uuid.uuid4())[:8]
        user_id = _extract_user_id(request)
        # Mcp-Session-Id lets us correlate every line of an MCP request to its
        # session — without it, diagnosing "state not held across calls" means
        # hand-correlating timestamps. None for non-MCP (web/API) requests.
        session_id = request.headers.get("mcp-session-id")
        start = time.monotonic()
        logged = False

        async def send_wrapper(message):
            nonlocal logged
            if message["type"] == "http.response.start" and not logged:
                logged = True
                logger.info(
                    "http",
                    method=request.method,
                    path=request.url.path,
                    status=message["status"],
                    duration_ms=round((time.monotonic() - start) * 1000),
                )
            await send(message)

        with logger.contextualize(request_id=request_id, user_id=user_id, session_id=session_id):
            await self._app(scope, receive, send_wrapper)


def _extract_user_id(request) -> str | None:
    """Best-effort user identity for logs. Web/API uses the session cookie; MCP
    uses the OAuth bearer token (resolved token -> client -> user_id). Never
    raises — logging must not break a request."""
    from kajet_turbo.dependencies import oauth_repo, session_repo

    cookie = request.cookies.get("kajet_session", "")
    if cookie:
        user = session_repo.get_user(cookie)
        if user:
            return user["email"]

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            row = oauth_repo.get_access_token(auth[7:].strip())
            if row:
                return oauth_repo.get_user_id_by_client(row["client_id"])
        except Exception:
            return None
    return None
