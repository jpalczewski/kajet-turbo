from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import logged_tool
from kajet_turbo.markdown import EditMode, EditSpec
from kajet_turbo.mcp.context import ACTIVE_WORKSPACE, ActiveWorkspace
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
from kajet_turbo.services.notes import EditBatchItem, NoteService
from kajet_turbo.shared.notes import MovedNoteResult
from kajet_turbo.workspace import temporal_kwargs


def build_write(note_service: NoteService) -> FastMCP:
    srv = FastMCP("notes-write")

    @srv.tool(**write_tool(tags={"notes", "crud"}))
    @logged_tool
    async def save_note(
        title: str,
        content: str,
        tags: list[str] | None = None,
        folder: str = "",
        occurred_at: str | None = None,
        period: str | None = None,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> SavedNoteResult:
        """Zapisuje nową notatkę w podanym folderze (domyślnie root).
        folder: opcjonalna ścieżka np. 'Projekty/Klient A'.
        Uwaga: content powinien zawierać rzeczywiste znaki nowej linii (\\n),
        nie literalne \\\\n."""
        result = await run_sync(
            note_service.save,
            ws.owner_id,
            ws.name,
            ws.path,
            title,
            content,
            tags or [],
            folder=folder,
            occurred_at=occurred_at,
            period=period,
        )
        await publish_workspace_changed(ws)
        return SavedNoteResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "crud"}))
    @logged_tool
    async def save_notes(
        notes: list[NoteInput],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> list[BatchNoteSuccess | BatchNoteError]:
        """Saves multiple notes at once, in one commit. Always use this instead of multiple
        save_note calls when adding 2+ notes. Best-effort: each note is validated
        independently; the per-note result is BatchNoteSuccess {index, note_id} or
        BatchNoteError {index, error}. Wikilinks to notes in the same batch resolve
        regardless of order. Search indexing (chunks/FTS/embeddings) is deferred to
        background jobs — a note saved here may not appear in search_notes immediately.
        content needs real newline characters (\\n), not literal \\\\n."""
        results = await run_sync(
            note_service.save_many,
            ws.owner_id,
            ws.name,
            ws.path,
            [n.model_dump() for n in notes],
        )
        await publish_workspace_changed(ws)
        return [
            BatchNoteSuccess.model_validate(r)
            if "note_id" in r
            else BatchNoteError(index=r["index"], error=r["error"])
            for r in results
        ]

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    @logged_tool
    async def edit_note(
        note_id: str,
        expected_sha: Annotated[
            str,
            Field(
                description="Aktualny HEAD sha notatki z get_note/get_note_history — dowód, że "
                "przed edycją widziałeś bieżącą wersję. Niezgodność odrzuca edycję."
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
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
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
            note_service.update,
            note_id,
            owner_id=ws.owner_id,
            ws_path=ws.path,
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
            clear_date_metadata=clear_date_metadata,
            **temporal_kwargs(  # ty: ignore[invalid-argument-type] - dict[str, str] spread vs update()'s heterogeneous kwargs; keys are always occurred_at/period
                occurred_at, period
            ),
        )
        if result.get("stale_sha"):
            return StaleVersion.model_validate(result)
        await publish_note_updated(ws, result["note_id"])
        return EditNoteSuccess.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    @logged_tool
    async def edit_notes(
        edits: list[NoteEditInput],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
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
        check_batch(edits, "edits", "edycji")
        result = await run_sync(
            note_service.edit_many,
            ws.owner_id,
            ws.name,
            ws.path,
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
        await publish_workspace_changed(ws)
        return EditNotesApplied.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "crud"}))
    @logged_tool
    async def move_note(
        note_id: str,
        folder: str,
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> MovedNoteResult:
        """Przenosi notatkę do folderu w aktywnym workspace, tworząc brakującą ścieżkę.
        folder: pełna ścieżka folderu lub pusty string dla root."""
        result = await run_sync(
            note_service.move,
            note_id,
            owner_id=ws.owner_id,
            ws_path=ws.path,
            folder=folder,
        )
        await publish_workspace_changed(ws)
        return MovedNoteResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    @logged_tool
    async def delete_note(
        note_id: str,
        expected_sha: Annotated[
            str,
            Field(
                description="Aktualny HEAD sha notatki z get_note/get_note_history — dowód, "
                "że przed usunięciem widziałeś bieżącą wersję. Niezgodność zwraca StaleVersion."
            ),
        ],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> DeletedNoteResult | StaleVersion:
        """Usuwa notatkę. Błąd gdy notatka nie istnieje. Wymaga expected_sha z
        get_note/get_note_history; przy niezgodności zwraca StaleVersion — doczytaj
        aktualną wersję i spróbuj ponownie z nowym sha."""
        result = await run_sync(
            note_service.delete,
            note_id,
            owner_id=ws.owner_id,
            ws_path=ws.path,
            expected_sha=expected_sha,
        )
        if result.get("stale_sha"):
            return StaleVersion.model_validate(result)
        await publish_workspace_changed(ws)
        return DeletedNoteResult(note_id=note_id)

    @srv.tool(**write_tool(tags={"notes", "crud"}, destructive=True))
    @logged_tool
    async def delete_notes(
        deletes: list[NoteDeleteInput],
        ws: ActiveWorkspace = ACTIVE_WORKSPACE,
    ) -> DeleteNotesApplied | DeleteNotesRejected:
        """Delete multiple notes in one Git commit and one DB transaction. All-or-nothing
        at validation: if ANY item in the batch is invalid (wrong note, duplicate note_id,
        stale expected_sha) the whole batch is rejected and NOTHING is deleted; errors
        {index, note_id, error} per item say why. Gating uses expected_sha — the note's
        last commit sha from get_note_history — proving the caller saw the current version
        before deleting. On a mismatch, call get_note_history to read the current version
        and retry. Max 50 deletes per call."""
        check_batch(deletes, "deletes", "usunięć")
        result = await run_sync(
            note_service.delete_many,
            ws.owner_id,
            ws.name,
            ws.path,
            [d.model_dump() for d in deletes],
        )
        if not result.get("applied"):
            return DeleteNotesRejected.model_validate(result)
        await publish_workspace_changed(ws)
        return DeleteNotesApplied.model_validate(result)

    return srv
