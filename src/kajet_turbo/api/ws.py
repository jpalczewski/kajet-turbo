import asyncio
import contextlib
import json
import time

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.exceptions import WebSocketException
from nanoid import generate
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
    # Bound for the whole connection, sender task included: asyncio copies the current
    # context into a new task, so every line this connection emits — events_claimed and
    # ws_claim_error included — carries the same conn_id without threading it through.
    # Same mechanism LoggingMiddleware uses to tag an HTTP request.
    conn_id = generate(size=8)
    with logger.contextualize(conn_id=conn_id):
        started = time.monotonic()
        await websocket.accept()
        logger.info("ws_connected", user_id=user["id"])

        async def _sender() -> None:
            while True:
                await asyncio.sleep(2.0)
                try:
                    events = await run_sync(event_repo.claim, user["id"], _WS_KINDS)
                except Exception as e:
                    logger.opt(exception=e).warning("ws_claim_error", user_id=user["id"])
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
            logger.info(
                "ws_disconnected",
                user_id=user["id"],
                duration_s=round(time.monotonic() - started, 1),
            )
