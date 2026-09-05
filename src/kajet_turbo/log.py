import asyncio
import hashlib
import inspect
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from functools import wraps

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_context
from loguru import logger

from kajet_turbo import identity
from kajet_turbo.cache import TtlCache, cache_enabled
from kajet_turbo.concurrency import run_sync
from kajet_turbo.perf import current as perf_current
from kajet_turbo.perf import perf_span

_HEALTH_PATHS = frozenset({"/healthz", "/readyz"})

# Path values are user-authored in several REST routes (workspace names and folder
# paths), so the HTTP log records the route template and only opts opaque identifiers
# back in. Keep this list deliberately narrow: an unknown future parameter must stay out
# of off-box logs until it has been reviewed as safe.
_SAFE_HTTP_PATH_PARAMS = frozenset({"job_id", "key_id", "note_id", "profile_id", "sha"})

# Short enough that a logout or a token revocation stops showing up in logs quickly,
# long enough to collapse a burst (SPA polling, a run of MCP tool calls) into one lookup.
_USER_ID_CACHE_TTL = 60.0

# FastMCP's mount() re-enters call_tool() at every mount level a tool sits behind, and
# each level independently logs the same "Error calling tool"/validation failure (see
# _dedup_key). The real-world spread between those duplicate lines is sub-millisecond;
# 5s is generous headroom without risking a bridge across two distinct requests.
_ERROR_DEDUP_TTL = 5.0


class _Missing:
    """Distinguishes a cache miss from a cached "this credential is nobody"."""

    __slots__ = ()


_MISS = _Missing()


@dataclass(frozen=True)
class _UserLookup:
    user_id: str | None
    cacheable: bool = True


class _ErrorDedup:
    """Holds the dedup cache as a class attribute instead of a module global, so
    setup_logging() can rebind it without a `global` statement.

    Set inside setup_logging(), not at import time: that makes KAJET_CACHE=0 actually
    take effect (env is read when setup_logging() runs) and gives each test its own
    cache instead of bleeding dedup state across tests within the TTL window."""

    cache: TtlCache[str, bool] | None = None


def _dedup_key(entry: dict) -> str:
    """Full rendered content minus the timestamp. A coarser key (e.g. level+msg+origin+
    request_id) would collapse two distinct failures that only differ in a bound field
    (say, two different note_ids) into one — this only ever collapses lines that are
    byte-identical apart from `ts`, matching what production actually shows for the
    mount-chain duplication this guards against."""
    return json.dumps({k: v for k, v in entry.items() if k != "ts"}, sort_keys=True, default=str)


def _json_sink(message) -> None:
    r = message.record
    entry = {
        "ts": r["time"].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "level": r["level"].name.lower(),
        "msg": r["message"],
        # Origin of the line. Without it a message cannot be attributed to an emitter at
        # all — which is why a third-party library logging the same text from several
        # places, or the same record reaching the sink more than once, is undiagnosable
        # from the logs alone. `logger` is the frame's module here; records intercepted
        # from stdlib logging override it via `extra` with the real stdlib logger name
        # (see _InterceptHandler), which is the more informative of the two.
        # `origin` rather than `src`: bound fields are spread last and would silently
        # clobber these, and `src` is already used as an event field elsewhere.
        "logger": r["name"],
        "origin": f"{r['module']}:{r['function']}:{r['line']}",
        **r["extra"],
    }
    if r["exception"]:
        t, v, _ = r["exception"]
        entry["error_type"] = t.__name__ if t else None
        entry["error_msg"] = str(v) if v else None
    # FastMCP's mount() makes every level of a tool's mount chain re-enter call_tool(),
    # and each level independently logs the same tool-call/argument-validation failure
    # with no fields (exc_info=False) — so one real failure becomes N identical lines,
    # one per mount level. Scoped to the "fastmcp." logger namespace specifically (not
    # every error/warning app-wide): our own code has call sites that legitimately log
    # byte-identical content twice in one request by coincidence rather than re-entry
    # (e.g. concurrency.py's slow_sync warning for two separate slow dispatches under
    # matching rounded timings) — deduping those would silently hide a second real
    # incident. KAJET_CACHE=0 restores the raw, undeduplicated stream.
    logger_name = entry.get("logger", "")
    if (
        entry["level"] in ("error", "warning")
        and entry.get("request_id")
        and isinstance(logger_name, str)
        and logger_name.startswith("fastmcp.")
        and _ErrorDedup.cache
    ):
        key = _dedup_key(entry)
        if _ErrorDedup.cache.get(key) is not None:
            return
        _ErrorDedup.cache.put(key, True)
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
        # Bind the stdlib logger name: loguru derives `record["name"]` from the frame, so
        # without this the emitting library ("fastmcp.server.server", "dulwich.config", …)
        # is lost and every intercepted line looks anonymous in the JSONL.
        logger.opt(depth=depth, exception=record.exc_info).bind(logger=record.name).log(
            level, record.getMessage()
        )


def setup_logging() -> None:
    _ErrorDedup.cache = TtlCache(ttl=_ERROR_DEDUP_TTL) if cache_enabled() else None
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.remove()
    logger.add(_json_sink, level=level)
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    # LoggingMiddleware already logs every non-health request as a structured "http"
    # event; uvicorn's own access log is pure duplication and floods LOG_LEVEL=DEBUG
    # with health-check lines. Always off.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    sql_level = logging.DEBUG if os.getenv("LOG_SQL") else logging.WARNING
    logging.getLogger("sqlalchemy.engine").setLevel(sql_level)
    # These libraries emit per-call/per-token internals at DEBUG with zero diagnostic
    # value in our JSONL logs (raw SSE payload dumps, TCP/TLS connect traces, parser
    # internals); pin them above DEBUG unconditionally, independent of LOG_LEVEL.
    # sse_starlette is not merely noisy: it debug-logs every outgoing SSE chunk, so with
    # LOG_LEVEL=DEBUG whole tool results — note titles and bodies included — end up in the
    # log store. dulwich emits four gitconfig lines per git operation and accounted for
    # 39% of MCP log volume in production.
    for noisy_logger in (
        "markdown_it",
        "httpx",
        "httpcore",
        "mcp",
        "asyncio",
        "sse_starlette",
        "dulwich",
    ):
        logging.getLogger(noisy_logger).setLevel(logging.INFO)
    # FastMCP uses stdlib logging with propagate=False and its own RichHandler.
    # Replace it with our InterceptHandler so FastMCP logs flow through loguru → JSONL.
    fastmcp_log = logging.getLogger("fastmcp")
    fastmcp_log.handlers.clear()
    fastmcp_log.addHandler(_InterceptHandler())
    fastmcp_log.propagate = False


def _handle_loop_exception(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Downgrade asyncio's own ERROR-level default handler for a ConnectionClosedError
    surfacing via asyncio.shield() (see asyncio.tasks._log_on_exception) to a warning.
    This is routine WebSocket keepalive churn — client-side idle-connection reapers,
    backgrounded mobile clients — not an application bug: by the time the shielded ping
    task fails, nothing is left awaiting it, so asyncio's default handler logs it at
    ERROR unconditionally. Every other exception still goes through that default handler,
    untouched.
    """
    exc = context.get("exception")
    message = context.get("message", "")
    if (
        exc is not None
        and type(exc).__name__ == "ConnectionClosedError"
        and "shielded future" in message
    ):
        logger.warning(
            "ws_shielded_connection_closed", error_type=type(exc).__name__, error_msg=str(exc)
        )
        return
    loop.default_exception_handler(context)


def install_loop_exception_handler() -> None:
    """Must be called from a running event loop (e.g. an ASGI lifespan)."""
    asyncio.get_running_loop().set_exception_handler(_handle_loop_exception)


# Tools slower than this log at WARNING for easy alerting/profiling. Tune via
# SLOW_TOOL_MS; set 0 to always log tool completions at INFO.
_SLOW_TOOL_MS = float(os.getenv("SLOW_TOOL_MS", "2000"))


def log_tool_error(tool: str, start: float) -> None:
    """Shared by logged_tool and ServiceErrorMiddleware (mcp/tooling.py): both need the
    same live-context session/request_id rebind logged_tool's success path already does
    below — the FastMCP session task captures middleware contextvars at session-init
    time, so ambient session_id/request_id are stale by the time either runs unless
    re-read from the live Context.
    """
    try:
        ctx = get_context()
    except RuntimeError:
        ctx = None
    bind: dict[str, str] = {}
    if ctx is not None:
        for key in ("session_id", "request_id"):
            try:
                val = getattr(ctx, key)
            except Exception:
                continue
            if val:
                bind[key] = val
    with logger.contextualize(**bind):
        logger.exception(tool, tool=tool, duration_ms=round((time.monotonic() - start) * 1000))


def logged_tool(fn):
    @wraps(fn)
    async def wrapper(*args, **kwargs):
        # Bind from the live tool context: the FastMCP session task captures the
        # middleware contextvars at session-init time, so without this, tool logs
        # would carry the initialize request's ids instead of this call's session.
        try:
            ctx = get_context()
        except RuntimeError:
            ctx = None
        bind: dict[str, str] = {}
        if ctx is not None:
            for key in ("session_id", "request_id"):
                try:
                    val = getattr(ctx, key)
                except Exception:
                    continue
                if val:
                    bind[key] = val
        # Late import: mcp.tooling -> repositories.git -> log would cycle at module
        # level (repositories/git.py imports `logger` from this module). Do not hoist.
        from kajet_turbo.mcp.tooling import SERVICE_ERRORS

        start = time.monotonic()
        with logger.contextualize(**bind), perf_span() as span:
            try:
                result = await fn(*args, **kwargs)
                duration_ms = round((time.monotonic() - start) * 1000)
                level = "warning" if _SLOW_TOOL_MS and duration_ms >= _SLOW_TOOL_MS else "info"
                extra = dict(span.fields) if span else {}
                logger.log(
                    level.upper(), fn.__name__, tool=fn.__name__, duration_ms=duration_ms, **extra
                )
                return result
            except ToolError:
                # Logged once by ServiceErrorMiddleware (mcp/tooling.py), which sees
                # every tool call including Depends resolution that never reaches this
                # wrapper (issue #71).
                raise
            except SERVICE_ERRORS as e:
                # Convert here, not in ServiceErrorMiddleware: fastmcp's own call_tool()
                # already wraps anything surviving tool._run() into a generic
                # ToolError(f"Error calling tool {name!r}: {e}") before the middleware's
                # on_call_tool ever runs (its try/except sits *inside* what call_next()
                # invokes) - see mcp/tooling.py. This wrapper sits directly around the
                # raw coroutine, so it's the one seam that still sees the original type.
                log_tool_error(fn.__name__, start)
                raise ToolError(str(e)) from e
            except Exception:
                log_tool_error(fn.__name__, start)
                raise

    return wrapper


def _http_route_fields(scope) -> dict[str, str]:
    """Return a useful route identity without user-authored URL segments."""
    route_path = getattr(scope.get("route"), "path", None)
    # Root-mounted SPA/static traffic has an empty template. Collapse it to "/"
    # and collapse unmatched traffic to a category instead of falling back to the
    # potentially private browser URL.
    safe_path = (route_path or "/") if isinstance(route_path, str) else "<unmatched>"
    return {
        "path": safe_path,
        **{
            key: str(value)
            for key, value in scope.get("path_params", {}).items()
            if key in _SAFE_HTTP_PATH_PARAMS
        },
    }


class LoggingMiddleware:
    """Raw ASGI middleware — safe for SSE/streaming unlike BaseHTTPMiddleware."""

    def __init__(self, app) -> None:
        self._app = app
        # Best-effort log tagging, never an authz decision — which is why the cache lives
        # here and not in `identity`. Keys are digests, so no live credential is retained;
        # values are opaque user IDs, never email addresses. Honors KAJET_CACHE like every
        # other cache so an operator debugging staleness can turn it off.
        self._user_ids: TtlCache[tuple[str, str], _UserLookup] | None = (
            TtlCache(ttl=_USER_ID_CACHE_TTL) if cache_enabled() else None
        )

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        from starlette.requests import Request

        request = Request(scope)
        is_health_path = request.url.path in _HEALTH_PATHS
        request_id = str(uuid.uuid4())[:8]
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
                status = message["status"]
                if not is_health_path or status >= 500:
                    level = "warning" if is_health_path and status >= 500 else "info"
                    _span = perf_current()
                    perf_fields = dict(_span.fields) if _span else {}
                    logger.log(
                        level.upper(),
                        "http",
                        method=request.method,
                        status=status,
                        duration_ms=round((time.monotonic() - start) * 1000),
                        **_http_route_fields(scope),
                        **perf_fields,
                    )
            await send(message)

        # Resolved before the span opens: tagging a log line is not the route's work, so
        # its limiter_wait_ms must not land on the route's http entry. One contextualize
        # then covers the whole request.
        user_id = None if is_health_path else await self._resolve_user_id(request, request_id)
        with (
            logger.contextualize(request_id=request_id, user_id=user_id, session_id=session_id),
            perf_span(),
        ):
            await self._app(scope, receive, send_wrapper)

    async def _resolve_user_id(self, request, request_id: str) -> str | None:
        """Best-effort identity for logs: session cookie for web/API, OAuth bearer for MCP."""
        cookie = identity.session_token_from_cookies(request.cookies)
        if cookie:
            user_id = await self._cached_user_id("session", cookie, request_id)
            if user_id is not None:
                return user_id

        bearer = identity.bearer_token_from_headers(request.headers)
        if bearer:
            return await self._cached_user_id("bearer", bearer, request_id)
        return None

    async def _cached_user_id(self, kind: str, credential: str, request_id: str) -> str | None:
        if self._user_ids is None:
            return (await run_sync(_lookup_user_id, kind, credential, request_id)).user_id

        key = (kind, hashlib.sha256(credential.encode()).hexdigest())
        cached = self._user_ids.get(key, _MISS)
        if not isinstance(cached, _Missing):
            return cached.user_id

        lookup = await run_sync(_lookup_user_id, kind, credential, request_id)
        if lookup.cacheable:
            self._user_ids.put(key, lookup)
        return lookup.user_id


def _lookup_user_id(kind: str, credential: str, request_id: str) -> _UserLookup:
    """Repository lookups for log tagging; runs in a worker thread via ``run_sync``.

    Never raises — logging must not break a request — but the failure is not silent
    either: a lookup that breaks for every request would otherwise look exactly like
    ordinary anonymous traffic.
    """
    # Late import, load-bearing twice: at module level `dependencies` would be an import
    # cycle, and the tests monkeypatch these repo singletons *on* that module. Do not hoist.
    from kajet_turbo.dependencies import oauth_repo, session_repo

    try:
        if kind == "session":
            user = identity.resolve_session_user(session_repo, credential)
            return _UserLookup(str(user["id"]) if user else None)
        return _UserLookup(identity.resolve_bearer_user_id(oauth_repo, credential))
    except Exception as e:
        logger.warning(
            "log_identity_lookup_failed",
            credential_kind=kind,
            error_type=type(e).__name__,
            request_id=request_id,
        )
        return _UserLookup(None, cacheable=False)
