from fastapi import APIRouter, Depends, HTTPException, Query

from kajet_turbo.api.schemas import ConsentRequest, ConsentResponse, PendingInfoResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import CurrentUser, get_provider, get_required_user
from kajet_turbo.errors import AuthError

router = APIRouter()


@router.post(
    "/api/consent",
    response_model=ConsentResponse,
    responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def api_consent(
    body: ConsentRequest,
    user: CurrentUser = Depends(get_required_user),
    provider=Depends(get_provider),
) -> ConsentResponse:
    # An empty/unknown pending_id reaches provider.complete_authorization like any other
    # value and raises ValueError there (get_pending finds no row) -- no separate blank
    # check needed before the call.
    try:
        redirect_uri = await provider.complete_authorization(body.pending_id, user.id)
    except ValueError:
        raise HTTPException(status_code=400, detail=AuthError.PENDING_EXPIRED) from None
    return ConsentResponse(redirect_uri=redirect_uri)


@router.get(
    "/api/pending",
    response_model=PendingInfoResponse,
    responses={404: {"model": ErrorResponse}},
)
async def api_pending_info(
    id: str = Query(...),
    provider=Depends(get_provider),
) -> PendingInfoResponse:
    """No auth dependency by design (docs/specs/rest-contracts.md) -- this is the
    pre-login OAuth consent screen's client-name lookup. Exempt from the rest of the
    #254 typed-endpoint migration per the issue (protocol-adjacent OAuth routes keep
    their wire shape); the 404 case reuses PENDING_EXPIRED since it's the same
    unknown/expired-pending_id condition as api_consent's."""
    client = await run_sync(provider.get_pending_client, id)
    if client is None:
        raise HTTPException(status_code=404, detail=AuthError.PENDING_EXPIRED)
    name = getattr(client, "client_name", None) or client.client_id
    return PendingInfoResponse(client_name=name)
