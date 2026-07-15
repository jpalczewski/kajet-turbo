import time
from collections.abc import Sequence
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp.types import ToolAnnotations

from kajet_turbo.api.schemas.ws import NoteUpdatedEvent, WorkspaceChangedEvent
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import event_repo
from kajet_turbo.mcp.context import ActiveWorkspace
from kajet_turbo.repositories.git import GitError


def read_tool(*, tags: set[str] | None = None) -> dict[str, Any]:
    return {
        "tags": {"read", *(tags or set())},
        "annotations": ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
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
            readOnlyHint=False,
            destructiveHint=destructive,
            idempotentHint=idempotent,
            openWorldHint=False,
        ),
    }


# One tuple for every notes tool. Catching a member where a given service call
# cannot raise it is harmless; anything outside the tuple is a programming
# error and must surface as an internal error, not a polite ToolError.
SERVICE_ERRORS = (GitError, ValueError, FileNotFoundError, FileExistsError)


class ServiceErrorMiddleware(Middleware):
    """Map domain/service exceptions to ToolError at the server boundary.

    Registered once on the root server in build_mcp; applies to all mounted
    sub-servers, replacing per-tool try/except blocks.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext):
        try:
            return await call_next(context)
        except SERVICE_ERRORS as e:
            raise ToolError(str(e)) from e


async def publish_workspace_changed(ws: ActiveWorkspace) -> None:
    """Notify the owner's WS clients that workspace contents changed (LLM write)."""
    await run_sync(
        event_repo.publish,
        ws.owner_id,
        "workspace_changed",
        WorkspaceChangedEvent(
            type="workspace_changed", owner_id=ws.owner_id, workspace=ws.name
        ).model_dump(),
    )


async def publish_note_updated(ws: ActiveWorkspace, note_id: str) -> None:
    """Notify the owner's WS clients that a single note changed in place."""
    await run_sync(
        event_repo.publish,
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
