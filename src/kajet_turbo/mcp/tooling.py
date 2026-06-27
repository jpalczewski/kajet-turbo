from typing import Any

from mcp.types import ToolAnnotations


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
