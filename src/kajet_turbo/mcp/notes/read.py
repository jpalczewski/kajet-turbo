from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from fastmcp.exceptions import ToolError
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.markdown import join_target
from kajet_turbo.mcp.context import (
    NOTE_TARGET,
    OPTIONAL_NOTE_TARGET,
    WORKSPACE_TARGET,
    require_user_id,
    resolve_notes,
    resolve_workspace_target,
)
from kajet_turbo.mcp.notes.types import (
    FolderContext,
    FolderExportResult,
    NoteListItem,
    NoteListResponse,
    NoteOutlineResult,
    NoteReadError,
)
from kajet_turbo.mcp.tooling import check_batch, read_tool, require_found
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.services.notes import NoteData, NoteService
from kajet_turbo.services.targets import (
    NoteTarget,
    TargetFailure,
    WorkspaceTarget,
)
from kajet_turbo.workspace import normalize_folder


def build_read(note_service: NoteService, folder_meta_repo: FolderMetaRepository) -> FastMCP:
    srv = FastMCP("notes-read")

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def get_note(
        note_id: str | None = None,
        title: Annotated[
            str | None,
            Field(
                description="Exact note title instead of note_id, e.g. '2026-08-22'. "
                "Provide note_id OR title."
            ),
        ] = None,
        folder: Annotated[
            str | None,
            Field(
                description="Narrows a title lookup — like a wikilink, a *suffix* of the "
                "path: 'backlog' matches 'kajet-turbo/backlog'. Omit to search the whole "
                "workspace. Do not combine with note_id."
            ),
        ] = None,
        workspace: Annotated[
            str | None,
            Field(
                description="Workspace to search in — required when addressing by title. "
                "Do not combine with note_id."
            ),
        ] = None,
        user_id: str = Depends(require_user_id),
        target: NoteTarget | None = OPTIONAL_NOTE_TARGET,
    ) -> NoteData:
        """Returns a note as an object with all fields. Errors when the note does not
        exist. This is the only source of a note's full, current content —
        search_notes returns only fragments (chunks), never the whole body; call
        get_note/get_notes whenever you need the exact text. Address by note_id, or
        by title (+ optional folder, and workspace, which is required when using
        title) — the latter shortens a typical journaling operation to one call. A
        title matching several notes returns an error listing the candidates;
        narrow it with folder or use note_id instead."""
        if note_id is not None:
            if title is not None:
                raise ToolError("Provide exactly one of note_id or title.")
            if folder is not None:
                raise ToolError("folder only works with title — omit it with note_id.")
            if workspace is not None:
                raise ToolError("workspace only works with title — omit it with note_id.")
            assert target is not None  # OPTIONAL_NOTE_TARGET resolves note_id when it is set
            return require_found(await run_sync(note_service.get_with_content, target), note_id)
        if title is None:
            raise ToolError("Provide note_id or title.")
        if workspace is None:
            raise ToolError("workspace is required when addressing by title.")
        resolved_workspace = await resolve_workspace_target(workspace, user_id)
        return require_found(
            await run_sync(
                note_service.get_with_content_by_title,
                title,
                folder,
                resolved_workspace,
            ),
            join_target(folder or "", title),
        )

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def get_notes(
        note_ids: list[str],
        user_id: str = Depends(require_user_id),
    ) -> list[NoteData | NoteReadError]:
        """Reads multiple notes in one call instead of N x get_note. Max 50 at a
        time. An unknown id becomes NoteReadError {note_id, error} instead of
        aborting the whole batch."""
        check_batch(note_ids, "note_ids", "note_id")
        resolved = await resolve_notes(user_id, note_ids)
        targets = [r for r in resolved if isinstance(r, NoteTarget)]
        target_results = await run_sync(note_service.get_many, targets) if targets else []
        target_iter = iter(target_results)
        output: list[NoteData | NoteReadError] = []
        for r in resolved:
            if isinstance(r, TargetFailure):
                note_id = note_ids[r.index] if r.index is not None else ""
                output.append(
                    NoteReadError(note_id=note_id, error=f"Note not found: note_id={note_id}")
                )
            else:
                item = next(target_iter)
                output.append(
                    item if isinstance(item, NoteData) else NoteReadError.model_validate(item)
                )
        return output

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def get_note_outline(
        note_id: str,
        target: NoteTarget = NOTE_TARGET,
    ) -> NoteOutlineResult:
        """Returns a note's structure (headings + section sizes) without content —
        for surgical edits without pulling the whole card into context. Paste each
        section's target_heading directly into edit_note(mode='replace_section',
        target_heading=...). ambiguous=true means that heading repeats in the
        document, so target_heading won't work (edit_note returns an ambiguity
        error) — use another mode instead (e.g. replace_text)."""
        result = require_found(await run_sync(note_service.get_outline, target), note_id)
        return NoteOutlineResult.model_validate(result)

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def list_notes(
        workspace: str,
        tags: list[str] | None = None,
        limit: int = 20,
        folder: Annotated[
            str | None,
            Field(
                description="Filter to notes in this folder only, e.g. "
                "'Projekty/Klient A'. Empty string = root."
            ),
        ] = None,
        sort: Annotated[
            Literal["default", "updated", "title", "created"],
            Field(
                description="'default' — recency globally, README-first natural title order "
                "inside a folder. 'updated'/'created' — always that recency order, even inside "
                "a folder. 'title' — natural title order (README-first), even globally."
            ),
        ] = "default",
        target: WorkspaceTarget = WORKSPACE_TARGET,
    ) -> NoteListResponse:
        """Returns a list of notes along with folder metadata (when set).
        workspace: the workspace name to operate in.
        folder: optional filter — only notes in this folder (e.g. 'Projects/Client A').
        The tags filter is OR and hierarchical: passing 'work' also matches notes
        tagged 'work/projects' etc. (segment-prefix matching).
        folder_context in the response carries instructions for the LLM when they
        are set for the folder."""
        notes = await run_sync(
            note_service.list_notes,
            target,
            tags=tags or None,
            limit=limit,
            folder=folder,
            sort=sort,
        )
        folder_context: FolderContext | None = None
        if folder is not None:
            meta = await run_sync(
                folder_meta_repo.get, target.owner_id, target.name, normalize_folder(folder)
            )
            if meta is not None:
                folder_context = FolderContext.model_validate(meta)
        return NoteListResponse(
            notes=[NoteListItem.model_validate(n) for n in notes],
            folder_context=folder_context,
        )

    @srv.tool(**read_tool(tags={"notes", "crud"}))
    @logged_tool
    async def export_folder(
        folder: str,
        workspace: str,
        max_chars: int = 80_000,
        target: WorkspaceTarget = WORKSPACE_TARGET,
    ) -> FolderExportResult:
        """Exports a whole folder (recursively, with subfolders) as one markdown
        document — for analyzing a corpus of N related notes at once, instead of N
        separate get_note calls. workspace: the workspace name to operate in.
        When max_chars is exceeded, it truncates at a note boundary (never
        mid-note); omitted notes come back in omitted. The first note is always
        included in full, even when it alone exceeds max_chars."""
        result = await run_sync(
            note_service.export_folder,
            target.name,
            owner_id=target.owner_id,
            ws_path=str(target.path),
            folder=folder,
            max_chars=max_chars,
        )
        return FolderExportResult.model_validate(result)

    return srv
