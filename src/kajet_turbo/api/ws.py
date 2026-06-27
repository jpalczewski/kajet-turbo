import asyncio
import contextlib
import json

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.exceptions import WebSocketException
from starlette import status

from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import get_event_repo, get_session_repo
from kajet_turbo.log import logger
from kajet_turbo.repositories.events import EventRepository
from kajet_turbo.repositories.sessions import SessionRepository

router = APIRouter()

_WS_KINDS = ["note_updated", "workspace_changed"]


async def _get_ws_user(
    websocket: WebSocket,
    session_repo: SessionRepository = Depends(get_session_repo),
) -> dict:
    token = websocket.cookies.get("kajet_session", "")
    user = await run_sync(session_repo.get_user, token) if token else None
    if not user:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    return user


@router.websocket("/api/ws")
async def ws_endpoint(
    websocket: WebSocket,
    user: dict = Depends(_get_ws_user),
    event_repo: EventRepository = Depends(get_event_repo),
) -> None:
    await websocket.accept()

    async def _sender() -> None:
        while True:
            await asyncio.sleep(2.0)
            try:
                events = await run_sync(event_repo.claim, user["id"], _WS_KINDS)
            except Exception:
                logger.warning("ws_claim_error", user_id=user["id"])
                continue
            for event in events:
                await websocket.send_json(json.loads(event.payload))

    sender = asyncio.create_task(_sender())
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender
