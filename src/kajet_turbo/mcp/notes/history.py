from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.mcp.context import NOTE_TARGET
from kajet_turbo.mcp.notes.types import (
    NoteLinkItem,
    NoteLinksResult,
    SavedNoteResult,
    StaleVersion,
)
from kajet_turbo.mcp.tooling import read_tool, require_found, write_tool
from kajet_turbo.services.notes import NoteData, NoteLinkService, NoteService
from kajet_turbo.services.targets import NoteTarget
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.shared.notes import HistoryEntry


def build_history(
    note_service: NoteService,
    link_service: NoteLinkService,
    workspace_service: WorkspaceService,
) -> FastMCP:
    srv = FastMCP("notes-history")

    @srv.tool(**read_tool(tags={"notes", "history"}))
    async def get_note_history(
        note_id: str,
        limit: int = 50,
        target: NoteTarget = NOTE_TARGET,
    ) -> list[HistoryEntry]:
        """Returns the note's version history.
        Each entry: {sha, message, timestamp}."""
        entries = await run_sync(note_service.get_history, target, limit)
        return [HistoryEntry.model_validate(e) for e in entries]

    @srv.tool(**read_tool(tags={"notes", "history"}))
    async def get_note_at_version(
        note_id: str,
        sha: str,
        target: NoteTarget = NOTE_TARGET,
    ) -> NoteData:
        """Returns the note's content at a specific git commit.
        sha: full or short commit hash from get_note_history."""
        version = await run_sync(note_service.get_version, target, sha)
        return NoteData.model_validate(version)

    @srv.tool(**write_tool(tags={"notes", "history"}, destructive=True))
    async def restore_note_version(
        note_id: str,
        sha: str,
        expected_sha: Annotated[
            str,
            Field(
                description="The note's current HEAD sha from get_note/get_note_history — "
                "proof you saw the version restore is about to overwrite. A mismatch "
                "returns StaleVersion."
            ),
        ],
        target: NoteTarget = NOTE_TARGET,
    ) -> SavedNoteResult | StaleVersion:
        """Restores the note to the version at the given commit.
        sha: full or short hash from get_note_history.
        expected_sha: HEAD sha — proof you saw the current version before it is
        overwritten; a mismatch returns StaleVersion."""
        result = await run_sync(
            note_service.restore_version,
            target,
            sha,
            expected_sha=expected_sha,
        )
        if result.get("stale_sha"):
            return StaleVersion.model_validate(result)
        return SavedNoteResult.model_validate(result)

    @srv.tool(**read_tool(tags={"notes", "links"}))
    async def get_note_links(
        note_id: str,
        include_meta: bool = False,
        include_cross_workspace: bool = True,
        target: NoteTarget = NOTE_TARGET,
    ) -> NoteLinksResult:
        """Returns outgoing and incoming links for the note.
        outlinks: notes this note links to.
        backlinks: notes that link to this note.
        include_meta=True adds tags and updated_at to each entry.
        include_cross_workspace=False restricts backlinks to the same workspace only.
        When an entry's workspace differs from this note's own workspace, it is a
        cross-workspace link; to write one in note content, use [[note:NOTE_ID]]
        (e.g. [[note:abc-123]]) instead of [[Title]]."""
        result = require_found(
            await run_sync(
                link_service.links,
                target,
                include_meta,
                include_cross_workspace,
            ),
            note_id,
        )
        return NoteLinksResult(
            outlinks=[NoteLinkItem.model_validate(link) for link in result["outlinks"]],
            backlinks=[NoteLinkItem.model_validate(link) for link in result["backlinks"]],
        )

    return srv
