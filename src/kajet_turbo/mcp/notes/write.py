from typing import Annotated

from fastmcp import FastMCP
from fastmcp.dependencies import Depends
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.markdown import EditMode, EditSpec
from kajet_turbo.mcp.context import (
    NOTE_TARGET_WRITE,
    WORKSPACE_TARGET_WRITE,
    require_user_id,
    resolve_notes_in_one_workspace,
)
from kajet_turbo.mcp.notes.types import (
    BatchNoteError,
    BatchNoteSuccess,
    DeletedNoteResult,
    DeleteNotesApplied,
    DeleteNotesRejected,
    EditNotesApplied,
    EditNotesRejected,
    EditNoteSuccess,
    NoteDeleteInput,
    NoteEditInput,
    NoteInput,
    SavedNoteResult,
    StaleVersion,
)
from kajet_turbo.mcp.tooling import (
    check_batch,
    publish_note_updated,
    publish_workspace_changed,
    write_tool,
)
from kajet_turbo.services.notes import (
    DeleteBatchItem,
    EditBatchItem,
    NoteCreateService,
    NoteDeleteService,
    NoteEditService,
)
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget
from kajet_turbo.workspace import temporal_kwargs


def build_write(
    note_create_service: NoteCreateService,
    note_edit_service: NoteEditService,
    note_delete_service: NoteDeleteService,
) -> FastMCP:
    srv = FastMCP("notes-write")

    @srv.tool(**write_tool(tags={"notes", "crud"}))
    async def save_note(
        title: str,
        content: str,
        workspace: str,
        tags: list[str] | None = None,
        folder: str = "",
        occurred_at: str | None = None,
        period: str | None = None,
        extras: Annotated[
            dict[str, object] | None,
            Field(
                description="Extra frontmatter fields beyond title/tags/dates, e.g. "
                "{'mood': 'ok'}. Keys must not shadow id/title/tags/created_at/updated_at/"
                "occurred_at/period."
            ),
        ] = None,
        target: WorkspaceTarget = WORKSPACE_TARGET_WRITE,
    ) -> SavedNoteResult:
        """Saves a new note in the given folder (root by default).
        workspace: the workspace name to save the note in.
        folder: optional path, e.g. 'Projects/Client A'.
        content must contain real newline characters (\\n), not literal \\\\n."""
        result = await run_sync(
            note_create_service.save,
            target,
            title,
            content,
            tags or [],
            folder=folder,
            occurred_at=occurred_at,
            period=period,
            extras=extras,
        )
        await publish_workspace_changed(target)
        return SavedNoteResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "crud"}))
    async def save_notes(
        notes: list[NoteInput],
        workspace: str,
        target: WorkspaceTarget = WORKSPACE_TARGET_WRITE,
    ) -> list[BatchNoteSuccess | BatchNoteError]:
        """Saves multiple notes at once, in one commit. Always use this instead of multiple
        save_note calls when adding 2+ notes. workspace: the workspace name to save the
        notes in. Best-effort: each note is validated independently; the per-note result
        is BatchNoteSuccess {index, note_id} or BatchNoteError {index, error}. Wikilinks to
        notes in the same batch resolve regardless of order. Search indexing (chunks/FTS/
        embeddings) is deferred to background jobs — a note saved here may not appear in
        search_notes immediately.
        content needs real newline characters (\\n), not literal \\\\n."""
        results = await run_sync(
            note_create_service.save_many,
            target,
            [n.model_dump() for n in notes],
        )
        await publish_workspace_changed(target)
        return [
            BatchNoteSuccess.model_validate(r)
            if "note_id" in r
            else BatchNoteError(index=r["index"], error=r["error"])
            for r in results
        ]

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    async def edit_note(
        note_id: str,
        expected_sha: Annotated[
            str,
            Field(
                description="The note's current HEAD sha from get_note/get_note_history — proof "
                "you saw the current version before editing. A mismatch rejects the edit."
            ),
        ],
        title: str | None = None,
        content: Annotated[
            str | None,
            Field(
                description="New body text for the whole-body modes (overwrite/append/prepend/"
                "replace_section). Omit it to edit only title/tags/folder and leave the body "
                "untouched. Not used by the text modes — those take new_str."
            ),
        ] = None,
        tags: list[str] | None = None,
        folder: str | None = None,
        occurred_at: str | None = None,
        period: str | None = None,
        clear_date_metadata: bool = False,
        mode: Annotated[
            EditMode,
            Field(
                description="How to edit the body. Whole-body modes take content: 'overwrite' "
                "(replace the whole body, default), 'append'/'prepend' (add at the end/start of "
                "the body, or of the target_heading section), 'replace_section' (replace the body "
                "of the target_heading section). Text modes take old_str: 'replace_text' (replace "
                "old_str with new_str), 'insert_after' (insert new_str right after the old_str "
                "anchor), 'delete_text' (remove old_str; takes no new_str). Passing a parameter "
                "another mode owns is an error, not a silent no-op."
            ),
        ] = "overwrite",
        extras: Annotated[
            dict[str, object] | None,
            Field(
                description="Extra frontmatter fields to merge into the note's existing "
                "extras: a key given here overwrites its previous value, existing keys not "
                "mentioned survive. Omit to leave extras untouched entirely. Keys must not "
                "shadow id/title/tags/created_at/updated_at/occurred_at/period."
            ),
        ] = None,
        target_heading: Annotated[
            str | None,
            Field(
                description="Section heading, e.g. '## Tasks'. Required for replace_section, "
                "optional for append/prepend, unused by every other mode."
            ),
        ] = None,
        old_str: Annotated[
            str | None,
            Field(
                description="Exact text to replace (replace_text), to delete (delete_text), or to "
                "anchor the insertion after (insert_after). Must be unique in the note unless "
                "replace_all is set."
            ),
        ] = None,
        new_str: Annotated[
            str | None,
            Field(
                description="Replacement for old_str (replace_text) or the text to insert after it "
                "(insert_after). Required by both; delete_text takes none."
            ),
        ] = None,
        replace_all: Annotated[
            bool,
            Field(
                description="With replace_text/delete_text: act on EVERY occurrence of old_str "
                "instead of requiring it to be unique. The response carries replaced with the "
                "count."
            ),
        ] = False,
        target: NoteTarget = NOTE_TARGET_WRITE,
    ) -> EditNoteSuccess | StaleVersion:
        """Edit a note. By default (mode='overwrite') it replaces the whole body with content;
        the surgical modes change a fragment without rewriting everything.
        Each mode owns exactly one parameter set: the whole-body modes take content, the text
        modes take old_str (+ new_str, except delete_text). Mixing them is a hard error.
        title/tags/folder can be changed independently of the body edit; passing folder moves
        the note. Omitting content with the default mode edits metadata only.
        content/new_str must carry real newlines (\\n), not literal \\\\n.
        expected_sha is the sha from get_note/get_note_history — proof you saw the current
        version. A mismatch returns StaleVersion: call get_note to re-read the note, then retry
        with the fresh sha."""
        result = await run_sync(
            note_edit_service.update,
            target,
            expected_sha=expected_sha,
            title=title,
            tags=tags,
            folder=folder,
            edit=EditSpec(
                mode=mode,
                content=content,
                target_heading=target_heading,
                old_str=old_str,
                new_str=new_str,
                replace_all=replace_all,
            ),
            extras=extras,
            clear_date_metadata=clear_date_metadata,
            **temporal_kwargs(  # ty: ignore[invalid-argument-type] - dict[str, str] spread vs update()'s heterogeneous kwargs; keys are always occurred_at/period
                occurred_at, period
            ),
        )
        if result.get("stale_sha"):
            return StaleVersion.model_validate(result)
        await publish_note_updated(target.workspace, result["note_id"])
        return EditNoteSuccess.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    async def edit_notes(
        edits: list[NoteEditInput],
        user_id: str = Depends(require_user_id),
    ) -> EditNotesApplied | EditNotesRejected:
        """Edit many notes in one atomic commit. All-or-nothing: if ANY edit in the batch is
        invalid (wrong note, broken wikilink, ambiguous target_heading/old_str, duplicate
        note_id, stale expected_sha) the whole batch is rejected and NOTHING is written;
        errors {index, note_id, error} says which item and why.
        Each item takes the same parameter split as edit_note: the whole-body modes take
        content, the text modes take old_str (+ new_str, except delete_text).
        Every item needs expected_sha — the note's sha from get_note/get_note_history,
        proof you saw the current version. On a stale one, call get_note to re-read the
        note and retry.
        Scope: content and tags only — no title/folder changes (use edit_note for those).
        Search indexing (chunks/FTS/embeddings) is deferred to background jobs — an edited
        note's search_notes results may lag briefly behind this call.
        Max 50 edits per call."""
        check_batch(edits, "edits", "edits")
        workspace, _ = await resolve_notes_in_one_workspace(user_id, [e.note_id for e in edits])
        result = await run_sync(
            note_edit_service.edit_many,
            workspace,
            [
                EditBatchItem(
                    note_id=e.note_id,
                    expected_sha=e.expected_sha,
                    edit=e.to_edit_spec(),
                    tags=e.tags,
                    occurred_at=e.occurred_at,
                    period=e.period,
                    clear_date_metadata=e.clear_date_metadata,
                )
                for e in edits
            ],
        )
        if not result.get("applied"):
            return EditNotesRejected.model_validate(result)
        await publish_workspace_changed(workspace)
        return EditNotesApplied.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    async def delete_note(
        note_id: str,
        expected_sha: Annotated[
            str,
            Field(
                description="The note's current HEAD sha from get_note/get_note_history — "
                "proof you saw the current version before deleting. A mismatch returns "
                "StaleVersion."
            ),
        ],
        target: NoteTarget = NOTE_TARGET_WRITE,
    ) -> DeletedNoteResult | StaleVersion:
        """Deletes a note. Errors when the note does not exist. Requires expected_sha from
        get_note/get_note_history; on a mismatch returns StaleVersion — re-read the current
        version and retry with the fresh sha."""
        result = await run_sync(
            note_delete_service.delete,
            target,
            expected_sha=expected_sha,
        )
        if result.get("stale_sha"):
            return StaleVersion.model_validate(result)
        await publish_workspace_changed(target.workspace)
        return DeletedNoteResult(note_id=note_id)

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    async def delete_notes(
        deletes: list[NoteDeleteInput],
        user_id: str = Depends(require_user_id),
    ) -> DeleteNotesApplied | DeleteNotesRejected:
        """Delete multiple notes in one Git commit and one DB transaction. All-or-nothing
        at validation: if ANY item in the batch is invalid (wrong note, duplicate note_id,
        stale expected_sha) the whole batch is rejected and NOTHING is deleted; errors
        {index, note_id, error} per item say why. Gating uses expected_sha — the note's
        last commit sha from get_note_history — proving the caller saw the current version
        before deleting. On a mismatch, call get_note_history to read the current version
        and retry. Max 50 deletes per call."""
        check_batch(deletes, "deletes", "deletes")
        workspace, _ = await resolve_notes_in_one_workspace(user_id, [d.note_id for d in deletes])
        result = await run_sync(
            note_delete_service.delete_many,
            workspace,
            [DeleteBatchItem(note_id=d.note_id, expected_sha=d.expected_sha) for d in deletes],
        )
        if not result.get("applied"):
            return DeleteNotesRejected.model_validate(result)
        await publish_workspace_changed(workspace)
        return DeleteNotesApplied.model_validate(result)

    return srv
