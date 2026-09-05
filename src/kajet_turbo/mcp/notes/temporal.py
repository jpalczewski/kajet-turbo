from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.context import ACTIVE_WORKSPACE, ActiveWorkspace
from kajet_turbo.mcp.notes.types import NoteListItem
from kajet_turbo.mcp.tooling import read_tool
from kajet_turbo.services.collections import CollectionService
from kajet_turbo.services.notes import NoteService


def build_temporal(note_service: NoteService, collection_service: CollectionService) -> FastMCP:
    srv = FastMCP("notes-temporal")

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def entries_in(
        period: Annotated[
            str,
            Field(
                description="Canonical period key: YYYY (year), YYYY-MM (month), YYYY-Www "
                "(ISO week, e.g. 2026-W12), or YYYY-MM-DD (day). Matches notes whose "
                "occurred_at falls inside this period, OR whose own period overlaps it — so "
                "a day query can also return the week/month/year note covering that day, not "
                "just day-grain notes. Invalid formats raise an error naming the bad value."
            ),
        ],
        folder: Annotated[
            str | None,
            Field(
                description="Restrict to notes in this folder and its descendants, e.g. "
                "'journal' matches 'journal' and 'journal/2026/03' but not 'journals-old'. "
                "Empty string restricts to root-level notes only (no descendants). Omit to "
                "search the whole workspace. Not combinable with collection."
            ),
        ] = None,
        collection: Annotated[
            str | None,
            Field(
                description="Restrict to a collection's folder instead of naming it "
                "directly — resolves to that collection's folder and its descendants, "
                "same semantics as folder. Not combinable with folder."
            ),
        ] = None,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> list[NoteListItem]:
        """List notes whose date falls within a calendar period, optionally narrowed to a
        folder or a collection. period/folder use the same semantics as the REST GET
        /entries endpoint; collection is sugar for folder that names a collection
        instead of a path — pass one or the other, never both."""
        if folder is not None and collection is not None:
            raise ValueError(
                "entries_in takes either folder or collection, not both — "
                f"got folder={folder!r} and collection={collection!r}."
            )
        if collection is not None:
            folder = await run_sync(collection_service.folder_prefix, ws.path, collection)
        notes = await run_sync(note_service.entries_in, ws.name, ws.owner_id, period, folder)
        return [NoteListItem.model_validate(n) for n in notes]

    return srv
