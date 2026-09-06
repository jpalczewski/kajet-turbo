from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.mcp.context import NOTE_TARGET, WORKSPACE_TARGET
from kajet_turbo.mcp.notes.types import GraphResult
from kajet_turbo.mcp.tooling import read_tool, require_found
from kajet_turbo.services.notes import NoteLinkService
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget
from kajet_turbo.services.workspaces import WorkspaceService


def build_graph(
    link_service: NoteLinkService,
    workspace_service: WorkspaceService,
) -> FastMCP:
    srv = FastMCP("notes-graph")

    @srv.tool(**read_tool(tags={"notes", "links", "graph"}))
    async def get_workspace_graph(
        workspace: str,
        include_tags: bool = False,
        limit: Annotated[
            int,
            Field(
                default=50,
                ge=1,
                le=200,
                description="Note nodes on this page, from 1 through 200.",
            ),
        ] = 50,
        offset: Annotated[
            int,
            Field(
                default=0,
                ge=0,
                description="Note nodes to skip; pass the previous page's next_offset.",
            ),
        ] = 0,
        target: WorkspaceTarget = WORKSPACE_TARGET,
    ) -> GraphResult:
        """Returns one page of the given workspace's note-link graph: notes as nodes
        (including notes with no links), each carrying in links the ids it points at, and
        broken/dangling links when this workspace has link validation disabled.

        Nodes are ranked by link degree, so the first page is the workspace's hubs and one
        call is usually enough to get oriented; use get_note_neighborhood to go deep on a
        single note instead of paging to reach it. total_nodes is the whole graph's size,
        and next_offset is the offset to pass for the next page — null on the last one.
        A page lists only the links whose source is on it, so walking every page yields
        each link exactly once and a link may name a node from another page.
        dangling_links covers this page's notes only; it is null when validation is on
        (nothing to track) or empty when validation is off and every link currently
        resolves. Set include_tags=true to add tag hubs, note-to-tag links and
        child-tag-to-parent-tag links for this page's notes; a hub shared by notes on
        different pages appears on each of them.
        workspace: the workspace name to build the graph for."""
        result = await run_sync(
            link_service.graph, target, include_tags, limit=limit, offset=offset
        )
        return GraphResult.model_validate(result, context={"workspace": target.name})

    @srv.tool(**read_tool(tags={"notes", "links", "graph"}))
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
        include_tags: bool = False,
        target: NoteTarget = NOTE_TARGET,
    ) -> GraphResult:
        """Returns a note's local graph as an induced directed subgraph: every node carries
        the ids it points at in links.

        The walk follows incoming and outgoing wikilinks. Set include_cross_workspace=true
        to follow [[note:ID]] links into the caller's other workspaces; it is off by default.
        Set include_tags=true to add hubs for tags on the returned notes and their ancestors.
        The result is never paged — next_offset is always null and the totals describe what
        was returned.
        """
        result = require_found(
            await run_sync(
                link_service.neighborhood,
                target,
                depth,
                include_cross_workspace,
                include_tags,
            ),
            note_id,
        )
        return GraphResult.model_validate(result, context={"workspace": target.workspace.name})

    return srv
