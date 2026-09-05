import os
import time
from collections.abc import Sequence
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.exceptions import ValidationError as FastMCPValidationError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp.types import ToolAnnotations
from pydantic import ValidationError as PydanticValidationError

from kajet_turbo.api.schemas.ws import NoteUpdatedEvent, WorkspaceChangedEvent
from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logger
from kajet_turbo.mcp.context import (
    McpDependencies,
    current_mcp_dependencies,
    use_mcp_context,
)
from kajet_turbo.perf import perf_span
from kajet_turbo.repositories.git import GitError, use_post_commit_hooks
from kajet_turbo.services.targets import WorkspaceTarget


def read_tool(*, tags: set[str] | None = None) -> dict[str, Any]:
    return {
        "tags": {"read", *(tags or set())},
        "annotations": ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    }


def write_tool(
    *,
    tags: set[str] | None = None,
    destructive: bool = False,
    idempotent: bool = False,
) -> dict[str, Any]:
    return {
        "tags": {"write", *(tags or set())},
        "annotations": ToolAnnotations(
            read_only_hint=False,
            destructive_hint=destructive,
            idempotent_hint=idempotent,
            open_world_hint=False,
        ),
    }


# One tuple for every notes tool. Catching a member where a given service call
# cannot raise it is harmless; anything outside the tuple is a programming
# error and must surface as an internal error, not a polite ToolError.
SERVICE_ERRORS = (GitError, ValueError, FileNotFoundError, FileExistsError)

# Tools slower than this log at WARNING for easy alerting/profiling. Tune via
# SLOW_TOOL_MS; set 0 to always log tool completions at INFO.
_SLOW_TOOL_MS = float(os.getenv("SLOW_TOOL_MS", "2000"))


def _correlation_ids(context: MiddlewareContext) -> dict[str, str]:
    """Read the ids off the live Context this call was dispatched with.

    Not the ambient contextvars: the FastMCP session task captures those once at
    session-init time, so on a persistent session they still carry the initialize
    request's ids by the time a tool runs (issue #71). `session_id` is absent on
    stateless requests, which is the normal case since #244 — `request_id` is what
    correlates a tool call with its HTTP request there.
    """
    ctx = context.fastmcp_context
    if ctx is None:
        return {}
    ids: dict[str, str] = {}
    for key in ("session_id", "request_id"):
        try:
            value = getattr(ctx, key)
        except Exception:
            continue
        if value:
            ids[key] = value
    return ids


def _param_paths(exc: PydanticValidationError) -> list[str]:
    """The paths pydantic rejected — never the values it rejected.

    A pydantic error renders the offending `input` in full, which on the argument side
    is whatever the caller sent (a note body, for `save_note`) and on the result side is
    the note we are about to return. Logs are shipped off-box, so the path is the only
    part of that detail that may be recorded — at the cost of a traceback we would
    otherwise keep, which the paths themselves usually replace well enough.
    """
    return [".".join(str(part) for part in err["loc"]) for err in exc.errors(include_url=False)]


def _rejected_params(exc: BaseException) -> list[str]:
    """`_param_paths` for a fastmcp ValidationError, which wraps pydantic's as __cause__."""
    cause = exc.__cause__
    return _param_paths(cause) if isinstance(cause, PydanticValidationError) else []


class ToolDispatchMiddleware(Middleware):
    """The single logging seam for `tools/call`: exactly one record per call.

    Registered once on the root server in `build_mcp`; applies to every mounted
    sub-server. It wraps the whole dispatch, so one hook covers dependency
    resolution, argument validation and the tool body alike — which is precisely
    what the per-tool logging wrapper it replaced could not do, since a `Depends`
    default (e.g. `NOTE_TARGET`) resolves before any wrapper around the tool function
    runs (issue #71).

    It also owns the `SERVICE_ERRORS` -> `ToolError` mapping. fastmcp's core wraps
    anything surviving `tool._run()` into a generic
    `ToolError(f"Error calling tool {name!r}: {e}")` *inside* the frame `call_next()`
    awaits, so the original type is already gone when this hook runs — but that
    wrapping sets `__cause__`, which still carries the original exception and with it
    the verbatim message `mcp/CLAUDE.md` promises the calling model.

    `duration_ms` measures the whole dispatch, validation and dependency resolution
    included, not just the tool body: that is the latency the client actually waits
    through, and `NOTE_TARGET` resolution does real database work.
    """

    def __init__(self, dependencies: McpDependencies | None = None):
        self._dependencies = dependencies

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext):
        tool = context.message.name
        start = time.monotonic()
        with logger.contextualize(**_correlation_ids(context)), perf_span() as span:
            try:
                result = await self._dispatch(context, call_next)
            except FastMCPValidationError as exc:
                # A malformed call, not a failed one: the caller got the schema wrong,
                # so WARNING, matching what fastmcp itself used to log it at. Also the
                # one branch that must never log its exception object — the rejected
                # value rides along inside it.
                self._log(
                    tool,
                    start,
                    span,
                    level="WARNING",
                    error_type="ValidationError",
                    rejected_params=_rejected_params(exc),
                )
                raise
            except PydanticValidationError as exc:
                # Not an argument failure — fastmcp converts those — but a model
                # validating inside the tool, e.g. SavedNoteResult over a result we are
                # about to return. That is our bug, not the caller's, so ERROR; but the
                # exception still carries the rejected value, so it is logged the same
                # payload-free way.
                self._log(
                    tool,
                    start,
                    span,
                    error_type="PydanticValidationError",
                    rejected_params=_param_paths(exc),
                )
                raise
            except ToolError as exc:
                cause = exc.__cause__
                if isinstance(cause, SERVICE_ERRORS):
                    self._log(tool, start, span, exception=cause)
                    raise ToolError(str(cause)) from cause
                # `cause is None` for a ToolError raised on purpose, by a tool body or
                # by a dependency; otherwise it is an unexpected exception fastmcp
                # wrapped, and its own type is the informative one to record.
                self._log(tool, start, span, exception=cause or exc)
                raise
            except Exception as exc:
                self._log(tool, start, span, exception=exc)
                raise
            duration_ms = _elapsed_ms(start)
            level = "WARNING" if _SLOW_TOOL_MS and duration_ms >= _SLOW_TOOL_MS else "INFO"
            logger.log(level, tool, tool=tool, duration_ms=duration_ms, **_span_fields(span))
            return result

    async def _dispatch(self, context: MiddlewareContext, call_next: CallNext):
        if self._dependencies is None:
            return await call_next(context)
        with (
            use_mcp_context(self._dependencies),
            use_post_commit_hooks(self._dependencies.post_commit_hooks),
        ):
            return await call_next(context)

    @staticmethod
    def _log(
        tool: str,
        start: float,
        span,
        *,
        exception: BaseException | None = None,
        level: str = "ERROR",
        **fields,
    ):
        logger.opt(exception=exception).log(
            level, tool, tool=tool, duration_ms=_elapsed_ms(start), **_span_fields(span), **fields
        )


def _elapsed_ms(start: float) -> int:
    return round((time.monotonic() - start) * 1000)


def _span_fields(span) -> dict[str, object]:
    return dict(span.fields) if span else {}


async def publish_workspace_changed(ws: WorkspaceTarget) -> None:
    """Notify the owner's WS clients that workspace contents changed (LLM write)."""
    await run_sync(
        current_mcp_dependencies().event_repo.publish,
        ws.owner_id,
        "workspace_changed",
        WorkspaceChangedEvent(
            type="workspace_changed", owner_id=ws.owner_id, workspace=ws.name
        ).model_dump(),
    )


async def publish_note_updated(ws: WorkspaceTarget, note_id: str) -> None:
    """Notify the owner's WS clients that a single note changed in place."""
    await run_sync(
        current_mcp_dependencies().event_repo.publish,
        ws.owner_id,
        "note_updated",
        NoteUpdatedEvent(
            type="note_updated",
            owner_id=ws.owner_id,
            workspace=ws.name,
            note_id=note_id,
            updated_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
        ).model_dump(),
    )


def check_batch(items: Sequence[object], field: str, unit: str, *, max_items: int = 50) -> None:
    if not items:
        raise ToolError(f"{field} cannot be empty.")
    if len(items) > max_items:
        raise ToolError(f"At most {max_items} {unit} per call (got {len(items)}).")


def require_found[T](result: T | None, note_id: str) -> T:
    if result is None:
        raise ToolError(f"Note not found: note_id={note_id}")
    return result
