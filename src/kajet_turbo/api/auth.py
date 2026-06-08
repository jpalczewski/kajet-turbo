from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from kajet_turbo.auth import verify_password
from kajet_turbo.dependencies import get_provider, get_session_repo, get_session_user, get_user_repo
from kajet_turbo.repositories.sessions import SessionRepository
from kajet_turbo.repositories.users import UserRepository

router = APIRouter()

_SESSION_COOKIE = "kajet_session"
_SESSION_MAX_AGE = 30 * 24 * 3600


@router.post("/api/login")
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

    user = user_repo.get_by_email(email)
    if not user or not verify_password(user.password_hash or "", password):
        return JSONResponse({"error": "Nieprawidłowy email lub hasło."}, status_code=401)

    session_token = session_repo.create(user.id)
    data: dict = {"email": user.email}

    if pending_id:
        try:
            data["redirect_uri"] = await provider.complete_authorization(pending_id, user.id)
        except ValueError:
            return JSONResponse({"error": "Wygasły pending_id."}, status_code=400)

    resp = JSONResponse(data)
    resp.set_cookie(_SESSION_COOKIE, session_token, max_age=_SESSION_MAX_AGE,
                    httponly=True, samesite="lax")
    return resp


@router.api_route("/api/session", methods=["GET", "DELETE"])
async def api_session(
    request: Request,
    session_repo: SessionRepository = Depends(get_session_repo),
) -> Response:
    if request.method == "DELETE":
        token = request.cookies.get(_SESSION_COOKIE, "")
        if token:
            session_repo.delete(token)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(_SESSION_COOKIE)
        return resp

    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    return JSONResponse({"email": user["email"]})
