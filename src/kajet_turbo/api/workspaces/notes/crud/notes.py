from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from kajet_turbo.api.schemas import (
    BatchCreateNotesRequest,
    BatchCreateNotesResponse,
    CreateNoteRequest,
    CreateNoteResponse,
    DeleteNoteResponse,
    MoveNoteRequest,
    MoveNoteResponse,
    NoteResult,
    NotesListResponse,
    NoteSort,
    UpdateNoteRequest,
    UpdateNoteResponse,
)
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.api.workspaces.notes._views import enrich_note_items
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import (
    RESOLVE_NOTE_WRITE,
    RESOLVE_WORKSPACE_WRITE,
    CurrentUser,
    get_note_create_service,
    get_note_delete_service,
    get_note_edit_service,
    get_note_folder_service,
    get_note_read_service,
    get_required_user,
    resolve_workspace_target,
)
from kajet_turbo.errors import NoteError
from kajet_turbo.markdown import BrokenWikilinkError, EditSpec
from kajet_turbo.services.notes import (
    NoteCreateService,
    NoteDeleteService,
    NoteEditService,
    NoteFolderService,
    NoteReadService,
)
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget
from kajet_turbo.workspace import (
    ExtrasReservedKeyError,
    InvalidFolderError,
    TemporalMetadataError,
    temporal_kwargs,
)

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.get(
    "/api/workspaces/{name}/notes",
    response_model=NotesListResponse,
)
def api_list_notes(
    name: str,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = Depends(resolve_workspace_target),
    note_read_service: NoteReadService = Depends(get_note_read_service),
    folder: str | None = None,
    tag: str | None = None,
    include_descendants: bool = True,
    sort: NoteSort = NoteSort.DEFAULT,
    limit: Annotated[int, Query(ge=1, le=500)] | None = None,
) -> NotesListResponse:
    notes = note_read_service.list_notes(
        workspace,
        tags=[tag] if tag is not None else None,
        # tag= has never honoured folder (the old tag-only lookup ignored it entirely) --
        # keep that exact pre-existing behaviour rather than silently starting to combine
        # them now that both go through the same call.
        folder=folder if tag is None else None,
        include_descendants=include_descendants,
        sort=sort,
        limit=limit,
    )
    return NotesListResponse(notes=enrich_note_items(str(workspace.path), notes))


@router.post(
    "/api/workspaces/{name}/notes",
    status_code=201,
    response_model=CreateNoteResponse,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def api_create_note(
    name: str,
    body: CreateNoteRequest,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = RESOLVE_WORKSPACE_WRITE,
    note_create_service: NoteCreateService = Depends(get_note_create_service),
) -> CreateNoteResponse:
    # BrokenWikilinkError/TemporalMetadataError are ValueError subclasses with their own
    # app-level handlers (api/errors.py) -- letting them propagate rather than catching
    # ValueError here keeps them mapped to their specific codes instead of ALREADY_EXISTS.
    try:
        result = await run_sync(
            note_create_service.save,
            workspace,
            body.title,
            body.content,
            body.tags,
            folder=body.folder,
            occurred_at=body.occurred_at,
            period=body.period,
            extras=body.extras,
        )
    except FileExistsError:
        raise HTTPException(status_code=409, detail=NoteError.ALREADY_EXISTS) from None
    return CreateNoteResponse(note_id=result["note_id"], warnings=result["warnings"])


@router.post(
    "/api/workspaces/{name}/notes/batch",
    response_model=BatchCreateNotesResponse,
    responses={422: {"model": ErrorResponse}},
)
async def api_create_notes_batch(
    name: str,
    body: BatchCreateNotesRequest,
    user: CurrentUser = Depends(get_required_user),
    workspace: WorkspaceTarget = RESOLVE_WORKSPACE_WRITE,
    note_create_service: NoteCreateService = Depends(get_note_create_service),
) -> BatchCreateNotesResponse:
    results = await run_sync(
        note_create_service.save_many, workspace, [note.model_dump() for note in body.notes]
    )
    return BatchCreateNotesResponse(results=[NoteResult(**r) for r in results])


@router.patch(
    "/api/workspaces/{name}/notes/{note_id}",
    response_model=UpdateNoteResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def api_update_note(
    name: str,
    note_id: str,
    body: UpdateNoteRequest,
    user: CurrentUser = Depends(get_required_user),
    target: NoteTarget = RESOLVE_NOTE_WRITE,
    note_edit_service: NoteEditService = Depends(get_note_edit_service),
) -> UpdateNoteResponse:
    try:
        result = await run_sync(
            note_edit_service.update,
            target,
            expected_sha=body.expected_sha,
            title=body.title,
            edit=EditSpec(content=body.content),
            tags=body.tags,
            folder=body.folder,
            extras=body.extras,
            clear_date_metadata=body.clear_date_metadata,
            # temporal_kwargs omits occurred_at/period entirely when None, so an omitted
            # or explicit-null value falls through to update()'s _UNCHANGED default
            # instead of being read as "clear this field" (see workspace.temporal_kwargs).
            **temporal_kwargs(  # ty: ignore[invalid-argument-type] - dict[str, str] spread vs update()'s heterogeneous kwargs; keys are always occurred_at/period
                body.occurred_at, body.period
            ),
        )
    except InvalidFolderError, TemporalMetadataError, BrokenWikilinkError, ExtrasReservedKeyError:
        raise
    except FileExistsError:
        raise HTTPException(status_code=409, detail=NoteError.ALREADY_EXISTS) from None
    except ValueError, FileNotFoundError:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND) from None
    if result.get("stale_sha"):
        raise HTTPException(
            status_code=409,
            detail={"error": str(NoteError.STALE_VERSION)},
        )
    # Keep the MCP-only replacement count private while exposing public link warnings.
    return UpdateNoteResponse(
        note_id=result["note_id"],
        warnings=result["warnings"],
        temporal_warnings=result["temporal_warnings"],
    )


@router.post(
    "/api/workspaces/{name}/notes/{note_id}/move",
    response_model=MoveNoteResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def api_move_note(
    name: str,
    note_id: str,
    body: MoveNoteRequest,
    user: CurrentUser = Depends(get_required_user),
    target: NoteTarget = RESOLVE_NOTE_WRITE,
    folder_service: NoteFolderService = Depends(get_note_folder_service),
) -> MoveNoteResponse:
    try:
        result = await run_sync(folder_service.move, target, body.folder)
    except InvalidFolderError:
        raise
    except FileExistsError:
        raise HTTPException(status_code=409, detail=NoteError.ALREADY_EXISTS) from None
    except ValueError, FileNotFoundError:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND) from None
    return MoveNoteResponse(**result)


@router.delete(
    "/api/workspaces/{name}/notes/{note_id}",
    response_model=DeleteNoteResponse,
    responses={404: {"model": ErrorResponse}},
)
async def api_delete_note(
    name: str,
    note_id: str,
    user: CurrentUser = Depends(get_required_user),
    target: NoteTarget = RESOLVE_NOTE_WRITE,
    note_delete_service: NoteDeleteService = Depends(get_note_delete_service),
) -> DeleteNoteResponse:
    try:
        await run_sync(note_delete_service.delete, target)
    except ValueError:
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND) from None
    return DeleteNoteResponse(ok=True)
