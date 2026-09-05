"""Shared helpers for MCP tool tests."""

import json
import re

# Matches anything that looks like a (short or full) git sha — used to assert
# stale-sha errors never leak the current sha. Floor is 8, not 7: note_ids are
# 7-char nanoids and an all-hex one would false-positive against this pattern.
SHA_LIKE = re.compile(r"\b[0-9a-f]{8,40}\b")


async def call_json(client, tool: str, args: dict | None = None):
    """Call an MCP tool and return its parsed JSON payload."""
    result = await client.call_tool(tool, args or {})
    return json.loads(result.content[0].text)


async def save_and_get_sha(
    client, title: str, content: str, tags: list[str] | None = None
) -> tuple[str, str]:
    """Save a note and return its (note_id, current sha).

    Every destructive tool is gated on a fresh expected_sha, so this is the opening move
    of most write-path tests.
    """
    note_id = (
        await call_json(
            client, "save_note", {"title": title, "content": content, "tags": tags or []}
        )
    )["note_id"]
    return note_id, (await call_json(client, "get_note", {"note_id": note_id}))["sha"]


async def seed_note(client, *, workspace: str, title: str, content: str = "", **kwargs) -> dict:
    """Save a note directly into `workspace` (the #248 explicit-workspace contract -- no
    activate_workspace call) and return the save_note result merged with its sha.

    Workspace-scoped counterpart to save_and_get_sha, for tests exercising tools that take
    an explicit `workspace` parameter instead of relying on session-activated state.
    """
    result = await call_json(
        client,
        "save_note",
        {"workspace": workspace, "title": title, "content": content, **kwargs},
    )
    sha = (await call_json(client, "get_note", {"note_id": result["note_id"]}))["sha"]
    return {**result, "sha": sha}
