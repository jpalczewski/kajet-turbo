from fastmcp import FastMCP

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.context import ACTIVE_WORKSPACE, ActiveWorkspace
from kajet_turbo.mcp.tooling import read_tool
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.shared.notes import GraphBase


class GraphResult(GraphBase):
    pass


def build_graph(
    note_service: NoteService,
    workspace_service: WorkspaceService,
) -> FastMCP:
    srv = FastMCP("notes-graph")

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

    return srv
