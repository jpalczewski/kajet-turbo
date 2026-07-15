from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from mcp.types import ToolAnnotations

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
