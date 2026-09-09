from fastapi import APIRouter, Depends, HTTPException

from kajet_turbo.api.schemas import (
    CreateShareLinkRequest,
    OkResponse,
    ShareLinkItem,
    ShareLinksResponse,
    UpdateShareLinkPreviewRequest,
)
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.dependencies import (
    CurrentUser,
    get_note_share_link_service,
    get_required_user,
    resolve_note_target,
)
from kajet_turbo.errors import ShareLinkError
from kajet_turbo.services.notes import NoteShareLinkService
from kajet_turbo.services.targets import NoteTarget

router = APIRouter(
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    }
)


@router.post(
    "/api/workspaces/{name}/notes/{note_id}/share-links",
    status_code=201,
    response_model=ShareLinkItem,
    responses={404: {"model": ErrorResponse}},
)
def api_create_share_link(
    name: str,
    note_id: str,
    body: CreateShareLinkRequest,
    user: CurrentUser = Depends(get_required_user),
    target: NoteTarget = Depends(resolve_note_target),
    svc: NoteShareLinkService = Depends(get_note_share_link_service),
) -> ShareLinkItem:
    return ShareLinkItem(**svc.create(target, preview_description=body.preview_description))


@router.get(
    "/api/workspaces/{name}/notes/{note_id}/share-links",
    response_model=ShareLinksResponse,
    responses={404: {"model": ErrorResponse}},
)
def api_list_share_links(
    name: str,
    note_id: str,
    user: CurrentUser = Depends(get_required_user),
    target: NoteTarget = Depends(resolve_note_target),
    svc: NoteShareLinkService = Depends(get_note_share_link_service),
) -> ShareLinksResponse:
    return ShareLinksResponse(links=[ShareLinkItem(**item) for item in svc.list_active(target)])


@router.patch(
    "/api/workspaces/{name}/notes/{note_id}/share-links/{token}",
    response_model=OkResponse,
    responses={404: {"model": ErrorResponse}},
)
def api_update_share_link_preview(
    name: str,
    note_id: str,
    token: str,
    body: UpdateShareLinkPreviewRequest,
    user: CurrentUser = Depends(get_required_user),
    target: NoteTarget = Depends(resolve_note_target),
    svc: NoteShareLinkService = Depends(get_note_share_link_service),
) -> OkResponse:
    if not svc.set_preview_description(target, token, body.preview_description):
        raise HTTPException(status_code=404, detail=ShareLinkError.NOT_FOUND)
    return OkResponse(ok=True)


@router.delete(
    "/api/workspaces/{name}/notes/{note_id}/share-links/{token}",
    response_model=OkResponse,
    responses={404: {"model": ErrorResponse}},
)
def api_revoke_share_link(
    name: str,
    note_id: str,
    token: str,
    user: CurrentUser = Depends(get_required_user),
    target: NoteTarget = Depends(resolve_note_target),
    svc: NoteShareLinkService = Depends(get_note_share_link_service),
) -> OkResponse:
    if not svc.revoke(target, token):
        raise HTTPException(status_code=404, detail=ShareLinkError.NOT_FOUND)
    return OkResponse(ok=True)
