from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from kajet_turbo.collections import Cardinality, CollectionDefinition
from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.mcp.collections.types import (
    CollectionResult,
    DefineCollectionResult,
    DeleteCollectionResult,
)
from kajet_turbo.mcp.context import ACTIVE_WORKSPACE, ActiveWorkspace
from kajet_turbo.mcp.tooling import publish_workspace_changed, read_tool, write_tool
from kajet_turbo.periods import PeriodKind
from kajet_turbo.services.collections import CollectionService
from kajet_turbo.services.workspaces import WorkspaceService


def _to_result(name: str, definition: CollectionDefinition) -> CollectionResult:
    return CollectionResult(
        name=name,
        grain=definition.grain,
        cardinality=definition.cardinality,
        folder=definition.folder,
        title=definition.title,
        description=definition.description,
    )


def build_collections(
    collection_service: CollectionService,
    workspace_service: WorkspaceService,
    state_store=None,
) -> FastMCP:
    srv = FastMCP("collections", session_state_store=state_store)

    @srv.tool(**write_tool(tags={"collections"}, idempotent=False))
    @logged_tool
    async def define_collection(
        name: str,
        grain: PeriodKind,
        cardinality: Cardinality,
        folder: Annotated[
            str,
            Field(
                description="Folder path template, e.g. 'weekly/{year}'. Placeholders: "
                "date, key, year, month (not for grain=year), ordinal (required iff "
                "cardinality=many)."
            ),
        ],
        title: Annotated[
            str,
            Field(description="Title template, same placeholders as folder."),
        ],
        description: Annotated[
            str | None,
            Field(default=None, description="Optional free-text note about this collection."),
        ] = None,
        dry_run: Annotated[
            bool,
            Field(
                description="If true, only compute affected_count/dropped for a "
                "redefinition and write nothing."
            ),
        ] = False,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> DefineCollectionResult:
        """Define a new collection, or redefine an existing one by name (add vs. update
        is decided by whether the name already exists — same call either way).

        Redefining an existing collection NEVER moves or renames note files: only
        which future notes count as members changes. Existing notes whose (folder,
        title) matched the old pattern but not the new one become loose notes —
        `affected_count`/`dropped` in the response report how many, they never block
        the write. Use dry_run=true to see that count before committing to it.

        Raises an error if the folder pattern would collide with another collection's
        (rendering the same folder for some period breaks folder-based collection
        lookup) — the error names the other collection.
        """
        result = await run_sync(
            collection_service.define_collection,
            ws.path,
            ws.name,
            ws.owner_id,
            name,
            grain,
            cardinality,
            folder,
            title,
            description,
            dry_run=dry_run,
        )
        if not result.get("would_write"):
            await publish_workspace_changed(ws)
        return DefineCollectionResult.model_validate(result)

    @srv.tool(**write_tool(tags={"collections"}, destructive=False, idempotent=True))
    @logged_tool
    async def delete_collection(
        name: str,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> DeleteCollectionResult:
        """Remove a collection definition. Non-destructive by construction: this only
        edits the collection's own definition — every note that was a member becomes a
        loose note, no note file is ever touched, moved, or deleted.
        """
        result = await run_sync(collection_service.delete_collection, ws.path, name)
        await publish_workspace_changed(ws)
        return DeleteCollectionResult.model_validate(result)

    @srv.tool(**read_tool(tags={"collections"}))
    @logged_tool
    async def list_collections(
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> list[CollectionResult]:
        """List every collection defined in the active workspace."""
        definitions = await run_sync(collection_service.list_collections, ws.path)
        return [_to_result(name, d) for name, d in definitions.items()]

    return srv
