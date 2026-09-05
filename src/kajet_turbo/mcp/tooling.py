import time
from collections.abc import Sequence
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp.types import ToolAnnotations

from kajet_turbo.api.schemas.ws import NoteUpdatedEvent, WorkspaceChangedEvent
from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import log_tool_error
from kajet_turbo.mcp.context import (
    ActiveWorkspace,
    McpDependencies,
    current_mcp_dependencies,
    use_mcp_context,
)
from kajet_turbo.repositories.git import GitError, use_post_commit_hooks


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
#
# Converted to ToolError inside logged_tool (log.py), not here: fastmcp's own
# call_tool() already wraps any exception surviving tool._run() into a ToolError
# before this middleware's on_call_tool ever sees it (its try/except sits *inside*
# what call_next() invokes — see call_tool(run_middleware=False)'s own core-logic
# try/except in fastmcp/server/server.py). A middleware-level `except SERVICE_ERRORS`
# is therefore unreachable; logged_tool sits directly around the raw coroutine and is
# the one seam that still sees the original exception type.
SERVICE_ERRORS = (GitError, ValueError, FileNotFoundError, FileExistsError)


class ServiceErrorMiddleware(Middleware):
    """Log a ToolError exactly once at the server boundary.

    Registered once on the root server in build_mcp; applies to all mounted
    sub-servers. Sees every ToolError regardless of where it originated: raised
    on purpose by a tool body or by a Depends dependency (e.g. ACTIVE_WORKSPACE,
    which resolves before logged_tool's wrapper ever runs), or wrapped by fastmcp
    around an unexpected exception that logged_tool already logged under its
    original type.
    """

    def __init__(self, dependencies: McpDependencies | None = None):
        self._dependencies = dependencies

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext):
        start = time.monotonic()
        try:
            if self._dependencies is None:
                return await call_next(context)
            with (
                use_mcp_context(self._dependencies),
                use_post_commit_hooks(self._dependencies.post_commit_hooks),
            ):
                return await call_next(context)
        except ToolError as e:
            # A ToolError raised on purpose has __cause__ is None. One fastmcp wraps
            # around a different exception carries that exception as __cause__ and was
            # already logged by logged_tool — logging it again here would double it
            # (issue #71).
            if e.__cause__ is None:
                log_tool_error(context.message.name, start)
            raise


async def publish_workspace_changed(ws: ActiveWorkspace) -> None:
    """Notify the owner's WS clients that workspace contents changed (LLM write)."""
    await run_sync(
        current_mcp_dependencies().event_repo.publish,
        ws.owner_id,
        "workspace_changed",
        WorkspaceChangedEvent(
            type="workspace_changed", owner_id=ws.owner_id, workspace=ws.name
        ).model_dump(),
    )


async def publish_note_updated(ws: ActiveWorkspace, note_id: str) -> None:
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
        raise ToolError(f"{field} nie może być puste.")
    if len(items) > max_items:
        raise ToolError(f"Maksymalnie {max_items} {unit} na wywołanie (podano {len(items)}).")


def require_found[T](result: T | None, note_id: str) -> T:
    if result is None:
        raise ToolError(f"Notatka {note_id} nie znaleziona.")
    return result
