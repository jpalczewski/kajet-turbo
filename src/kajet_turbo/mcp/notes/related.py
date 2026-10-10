from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from kajet_turbo.mcp.context import NOTE_TARGET
from kajet_turbo.mcp.tooling import read_tool, require_found
from kajet_turbo.services.notes.related import MAX_LIMIT, MIN_LIMIT, NoteRelatedService
from kajet_turbo.services.targets import NoteTarget
from kajet_turbo.shared.notes import RelatedNotesResponse


def build_related(related_service: NoteRelatedService) -> FastMCP:
    srv = FastMCP("notes-related")

    @srv.tool(**read_tool(tags={"notes", "search", "related"}))
    async def get_related_notes(
        note_id: str,
        folder: Annotated[
            str | None,
            Field(
                description="Restrict candidates to notes in this folder and its descendants, "
                "for example 'Projects/Client A'."
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                default=10,
                ge=MIN_LIMIT,
                le=MAX_LIMIT,
                description=f"Maximum related notes to return, from {MIN_LIMIT} through "
                f"{MAX_LIMIT}.",
            ),
        ] = 10,
        target: NoteTarget = NOTE_TARGET,
    ) -> RelatedNotesResponse:
        """Returns notes semantically related to an existing note, ranked best first, from
        the same workspace as that note.

        Use this when you already hold a note_id and want its semantic neighbours. It
        compares the note's stored chunk embeddings and makes no new embedding request, so
        it is cheap. Use search_notes instead when you start from query text rather than a
        note, and get_note_neighborhood for notes connected by explicit wikilinks.

        status says whether ranking ran: `ready` means it did (items may still be empty
        when nothing is related); `pending` means the note is not embedded yet — retry
        after indexing; `unavailable` means no embedding profile is configured; `empty`
        means the note has no content to compare. Only `ready` carries items.

        Each item names the related note and the best-matching chunk pair: target_content
        is a fragment of the related note, not the whole note — call get_note or get_notes
        for complete content. best_distance, hub_margin, coverage and score are raw
        ranking metrics, not percentages; compare them only against each other.

        The result is a bounded top-k with no continuation: there is no next page. Raise
        limit (up to 50) to see more."""
        result = require_found(
            await related_service.related_async(
                target.note_id,
                target.workspace.owner_id,
                target.workspace.name,
                folder=folder,
                limit=limit,
            ),
            note_id,
        )
        return RelatedNotesResponse.model_validate(result, from_attributes=True)

    return srv
