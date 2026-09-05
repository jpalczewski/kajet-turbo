from fastmcp import FastMCP

from kajet_turbo.concurrency import run_sync
from kajet_turbo.mcp.context import WORKSPACE_TARGET
from kajet_turbo.mcp.tooling import write_tool
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.targets import WorkspaceTarget
from kajet_turbo.shared.notes import ReindexResult


def build_maintenance(note_service: NoteService) -> FastMCP:
    srv = FastMCP("notes-maintenance")

    @srv.tool(**write_tool(tags={"notes", "index"}, idempotent=True))
    async def reindex_workspace(
        workspace: str,
        target: WorkspaceTarget = WORKSPACE_TARGET,
    ) -> ReindexResult:
        """Reconciles the SQLite index against the .md files in the given workspace:
        repairs drifted or missing rows without wiping and rebuilding. Refuses (raises)
        if it would delete an unusually large share of the workspace's notes — that
        signals a path or mount problem worth investigating before retrying.
        Row reconciliation is synchronous, but chunk/FTS/embedding rebuild for affected
        notes runs in background jobs afterward — search_notes may lag this call briefly.

        workspace: the workspace name to reindex."""
        result = await run_sync(
            note_service.reindex,
            target.name,
            owner_id=target.owner_id,
            ws_path=str(target.path),
        )
        return ReindexResult.model_validate(result)

    return srv
