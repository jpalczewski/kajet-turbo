from dataclasses import asdict
from datetime import date as _date
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
    OpenEntryResult,
)
from kajet_turbo.mcp.context import ACTIVE_WORKSPACE, ActiveWorkspace
from kajet_turbo.mcp.tooling import publish_workspace_changed, read_tool, write_tool
from kajet_turbo.periods import PeriodKind
from kajet_turbo.services.collections import CollectionService
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.shared.notes import NoteListItem


def _to_result(name: str, definition: CollectionDefinition) -> CollectionResult:
    # ``name`` is the collections.yaml mapping key, not necessarily identical to
    # ``definition.name`` (e.g. a hand-edited key with stray whitespace parses to a
    # stripped ``definition.name`` — see _parse_definition) — override explicitly
    # rather than trusting the two to always agree.
    return CollectionResult.model_validate({**asdict(definition), "name": name})


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

    @srv.tool(**write_tool(tags={"collections"}, destructive=False, idempotent=False))
    @logged_tool
    async def open_entry(
        collection: Annotated[
            str, Field(description="Name of the collection to open an entry in.")
        ],
        date: Annotated[
            str,
            Field(description="ISO calendar date (YYYY-MM-DD) the entry is addressed by."),
        ],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> OpenEntryResult:
        """Resolve or create a collection's entry for a date.

        For a `cardinality="one"` collection this is idempotent: calling it again for the
        same date always returns the same note (`created=false`) instead of risking a
        duplicate alongside it — the natural way to fill in a past entry. For
        `cardinality="many"` it always creates a new entry and allocates the next ordinal,
        since entries at that cardinality are logged, not addressed by date alone.

        Creates an empty note with no content — this does not apply a template.
        """
        try:
            when = _date.fromisoformat(date)
        except ValueError as exc:
            raise ValueError(
                f"date must be an ISO calendar date (YYYY-MM-DD), got {date!r}."
            ) from exc
        result = await run_sync(
            collection_service.open_entry, ws.path, ws.name, ws.owner_id, collection, when
        )
        if result["created"]:
            await publish_workspace_changed(ws)
        return OpenEntryResult.model_validate(result)

    @srv.tool(**read_tool(tags={"collections"}))
    @logged_tool
    async def list_collection_entries(
        collection: Annotated[str, Field(description="Name of the collection to list.")],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> list[NoteListItem]:
        """List every note that currently belongs to a collection, across all dates.

        Membership means the note's (folder, title) actually matches what the
        collection's pattern renders for some period — a note that merely lives under
        the collection's folder without a matching title is not included. Unlike
        `entries_in(collection=...)`, this takes no period: it returns the whole
        collection's history in one call.
        """
        entries = await run_sync(
            collection_service.list_entries, ws.path, ws.name, ws.owner_id, collection
        )
        return [NoteListItem.model_validate(n) for n in entries]

    return srv
