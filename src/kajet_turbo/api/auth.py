from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from kajet_turbo import identity
from kajet_turbo.api.schemas import LoginResponse, OkResponse, SessionResponse
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
from kajet_turbo.errors import SecurityEvent, SecurityReason
from kajet_turbo.log import log_security_event, logger
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.sessions import SessionRepository
from kajet_turbo.repositories.users import UserRepository

router = APIRouter()

_SESSION_COOKIE = identity.SESSION_COOKIE
_SESSION_MAX_AGE = 30 * 24 * 3600


@router.post("/api/login", response_model=LoginResponse)
async def api_login(
    request: Request,
    user_repo: UserRepository = Depends(get_user_repo),
    session_repo: SessionRepository = Depends(get_session_repo),
    provider=Depends(get_provider),
) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    email = str(body.get("email", ""))
    password = str(body.get("password", ""))
    pending_id = str(body.get("pending_id", ""))

    user = await run_sync(user_repo.get_by_email, email)
    password_hash = user.password_hash if user and user.password_hash else DUMMY_PASSWORD_HASH
    password_ok = await run_sync(verify_password, password_hash, password)
    if not user or not password_ok:
        failure_reason = SecurityReason.BAD_CREDENTIALS if user else SecurityReason.UNKNOWN_EMAIL
        log_security_event(
            SecurityEvent.AUTH_FAILURE,
            level="WARNING",
            user_id=user.id if user else None,
            auth_method="password",
            reason=failure_reason.value,
        )
        return JSONResponse({"error": "Nieprawidłowy email lub hasło."}, status_code=401)

    session_token = await run_sync(session_repo.create, user.id)
    data: dict = {"email": user.email}

    if pending_id:
        try:
            data["redirect_uri"] = await provider.complete_authorization(pending_id, user.id)
        except ValueError:
            log_security_event(
                SecurityEvent.AUTH_FAILURE,
                level="WARNING",
                user_id=user.id,
                auth_method="password",
                reason=SecurityReason.EXPIRED_PENDING.value,
            )
            return JSONResponse({"error": "Wygasły pending_id."}, status_code=400)

    log_security_event(
        SecurityEvent.AUTH_SUCCESS,
        level="INFO",
        user_id=user.id,
        auth_method="password",
    )
    resp = JSONResponse(data)
    resp.set_cookie(
        _SESSION_COOKIE, session_token, max_age=_SESSION_MAX_AGE, httponly=True, samesite="lax"
    )
    return resp


@router.get("/api/session", response_model=SessionResponse)
async def api_session_get(user: CurrentUser = Depends(get_required_user)) -> Response:
    return JSONResponse(
        {
            "email": user.email,
            "preferences": {"timezone": user.timezone, "locale": user.locale},
        }
    )


@router.delete("/api/session", response_model=OkResponse)
async def api_session_delete(
    request: Request,
    session_repo: SessionRepository = Depends(get_session_repo),
) -> Response:
    token = request.cookies.get(_SESSION_COOKIE, "")
    if token:
        await run_sync(session_repo.delete, token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_SESSION_COOKIE)
    return resp


@router.delete("/api/sessions", response_model=OkResponse)
async def api_sessions_delete(
    user: CurrentUser = Depends(get_required_user),
    oauth_repo: OAuthRepository = Depends(get_oauth_repo),
    session_repo: SessionRepository = Depends(get_session_repo),
) -> Response:
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
    response = JSONResponse({"ok": True})
    response.delete_cookie(_SESSION_COOKIE)
    return response
