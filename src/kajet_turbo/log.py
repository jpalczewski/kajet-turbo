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
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # replaced by LoggingMiddleware
    sql_level = logging.DEBUG if os.getenv("LOG_SQL") else logging.WARNING
    logging.getLogger("sqlalchemy.engine").setLevel(sql_level)
    # FastMCP uses stdlib logging with propagate=False and its own RichHandler.
    # Replace it with our InterceptHandler so FastMCP logs flow through loguru → JSONL.
    fastmcp_log = logging.getLogger("fastmcp")
    fastmcp_log.handlers.clear()
    fastmcp_log.addHandler(_InterceptHandler())
    fastmcp_log.propagate = False


def logged_tool(fn):
    @wraps(fn)
    async def wrapper(*args, **kwargs):
        start = time.monotonic()
        try:
            result = await fn(*args, **kwargs)
            logger.info(fn.__name__, tool=fn.__name__,
                        duration_ms=round((time.monotonic() - start) * 1000))
            return result
        except Exception:
            logger.exception(fn.__name__, tool=fn.__name__,
                             duration_ms=round((time.monotonic() - start) * 1000))
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

        with logger.contextualize(request_id=request_id, user_id=user_id):
            await self._app(scope, receive, send_wrapper)


def _extract_user_id(request) -> str | None:
    from kajet_turbo.dependencies import session_repo

    token = request.cookies.get("kajet_session", "")
    if not token:
        return None
    user = session_repo.get_user(token)
    return user["email"] if user else None
