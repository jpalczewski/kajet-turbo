from fastapi import APIRouter, Depends, HTTPException, Request, Response

from kajet_turbo import identity
from kajet_turbo.api.schemas import LoginRequest, LoginResponse, OkResponse, SessionResponse
from kajet_turbo.api.schemas.errors import ErrorResponse
from kajet_turbo.api.schemas.preferences import UserPreferences
from kajet_turbo.auth import DUMMY_PASSWORD_HASH, verify_password
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import (
    CurrentUser,
    get_oauth_repo,
    get_provider,
    get_required_user,
    get_session_repo,
    get_user_repo,
)
from kajet_turbo.errors import AuthError, SecurityEvent, SecurityReason
from kajet_turbo.log import log_security_event, logger
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.sessions import SessionRepository
from kajet_turbo.repositories.users import UserRepository

router = APIRouter()

_SESSION_COOKIE = identity.SESSION_COOKIE
_SESSION_MAX_AGE = 30 * 24 * 3600


@router.post(
    "/api/login",
    response_model=LoginResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def api_login(
    body: LoginRequest,
    response: Response,
    user_repo: UserRepository = Depends(get_user_repo),
    session_repo: SessionRepository = Depends(get_session_repo),
    provider=Depends(get_provider),
) -> LoginResponse:
    user = await run_sync(user_repo.get_by_email, body.email)
    password_hash = user.password_hash if user and user.password_hash else DUMMY_PASSWORD_HASH
    # Timing-safe: verify_password always runs, even for an unknown email, so a login
    # attempt's response time doesn't leak whether the address is registered.
    password_ok = await run_sync(verify_password, password_hash, body.password)
    if not user or not password_ok:
        failure_reason = SecurityReason.BAD_CREDENTIALS if user else SecurityReason.UNKNOWN_EMAIL
        log_security_event(
            SecurityEvent.AUTH_FAILURE,
            level="WARNING",
            user_id=user.id if user else None,
            auth_method="password",
            reason=failure_reason.value,
        )
        raise HTTPException(status_code=401, detail=AuthError.INVALID_CREDENTIALS)

    session_token = await run_sync(session_repo.create, user.id)

    redirect_uri = None
    if body.pending_id:
        try:
            redirect_uri = await provider.complete_authorization(body.pending_id, user.id)
        except ValueError:
            log_security_event(
                SecurityEvent.AUTH_FAILURE,
                level="WARNING",
                user_id=user.id,
                auth_method="password",
                reason=SecurityReason.EXPIRED_PENDING.value,
            )
            # Session row is already created but the cookie below is never set -- matches
            # the pre-migration behavior of returning before Set-Cookie on this branch.
            raise HTTPException(status_code=400, detail=AuthError.PENDING_EXPIRED) from None

    log_security_event(
        SecurityEvent.AUTH_SUCCESS,
        level="INFO",
        user_id=user.id,
        auth_method="password",
    )
    response.set_cookie(
        _SESSION_COOKIE, session_token, max_age=_SESSION_MAX_AGE, httponly=True, samesite="lax"
    )
    return LoginResponse(email=user.email, redirect_uri=redirect_uri)


@router.get("/api/session", response_model=SessionResponse)
async def api_session_get(user: CurrentUser = Depends(get_required_user)) -> SessionResponse:
    return SessionResponse(
        email=user.email,
        preferences=UserPreferences(timezone=user.timezone, locale=user.locale),
    )


@router.delete("/api/session", response_model=OkResponse)
async def api_session_delete(
    request: Request,
    response: Response,
    session_repo: SessionRepository = Depends(get_session_repo),
) -> OkResponse:
    token = request.cookies.get(_SESSION_COOKIE, "")
    if token:
        await run_sync(session_repo.delete, token)
    response.delete_cookie(_SESSION_COOKIE)
    return OkResponse(ok=True)


@router.delete("/api/sessions", response_model=OkResponse)
async def api_sessions_delete(
    response: Response,
    user: CurrentUser = Depends(get_required_user),
    oauth_repo: OAuthRepository = Depends(get_oauth_repo),
    session_repo: SessionRepository = Depends(get_session_repo),
) -> OkResponse:
    """Sign the current user out of every browser and connected OAuth client."""
    user_id = str(user.id)
    # Revoke OAuth first. If deleting browser sessions then fails, the still-valid cookie
    # lets the user safely retry this idempotent operation.
    oauth_count = await run_sync(oauth_repo.delete_credentials_by_user, user_id)
    session_count = await run_sync(session_repo.delete_all_for_user, user_id)
    logger.info(
        "user_signed_out_everywhere",
        user_id=user_id,
        oauth_credentials=oauth_count,
        sessions=session_count,
    )
    response.delete_cookie(_SESSION_COOKIE)
    return OkResponse(ok=True)
