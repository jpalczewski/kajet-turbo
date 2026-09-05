from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.context import ACTIVE_WORKSPACE, ActiveWorkspace
from kajet_turbo.mcp.tooling import read_tool, require_found
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.shared.notes import GraphBase


class GraphResult(GraphBase):
    pass


def build_graph(
    note_service: NoteService,
    workspace_service: WorkspaceService,
    state_store=None,
) -> FastMCP:
    srv = FastMCP("notes-graph", session_state_store=state_store)

    @srv.tool(**read_tool(tags={"notes", "links", "graph"}))
    @logged_tool
    async def get_workspace_graph(
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> GraphResult:
        """Returns the whole active workspace's note-link graph: every note as a node
        (including notes with no links), every resolved wikilink as an edge, and
        broken/dangling links when this workspace has link validation disabled.
        dangling_links is null when validation is on (nothing to track) or empty when
        validation is off and every link currently resolves."""
        result = await run_sync(note_service.graph, ws.name, ws.owner_id)
        return GraphResult.model_validate(result)

    @srv.tool(**read_tool(tags={"notes", "links", "graph"}))
    @logged_tool
    async def get_note_neighborhood(
        note_id: str,
        depth: Annotated[
            int,
            Field(
                default=2,
                ge=1,
                le=3,
                description="Number of undirected link hops to include, from 1 through 3.",
            ),
        ] = 2,
        include_cross_workspace: bool = False,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> GraphResult:
        """Returns a note's local graph as an induced directed subgraph.

        The walk follows incoming and outgoing wikilinks. Set include_cross_workspace=true
        to follow [[note:ID]] links into the caller's other workspaces; it is off by default.
        """
        result = require_found(
            await run_sync(
                note_service.neighborhood,
                note_id,
                ws.name,
                ws.owner_id,
                depth,
                include_cross_workspace,
            ),
            note_id,
        )
        return GraphResult.model_validate(result)

    return srv
