import asyncio
import contextlib
import json
import time

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from fastapi.exceptions import WebSocketException
from nanoid import generate
from starlette import status

from kajet_turbo import identity
from kajet_turbo.concurrency import run_sync
from kajet_turbo.dependencies import get_event_repo, get_session_repo
from kajet_turbo.log import logger
from kajet_turbo.repositories.events import EventRepository
from kajet_turbo.repositories.sessions import SessionRepository

router = APIRouter()

_WS_KINDS = ["note_updated", "workspace_changed"]

# Outbox poll cadence and session re-validation cadence are separate policies that
# happen to share one loop in _sender — kept as independent constants (not "every N
# polls") so changing one can't silently change the other. Tests monkeypatch both to
# speed up: see tests/api/test_ws.py.
_POLL_INTERVAL_S = 2.0
_REVALIDATE_INTERVAL_S = 30.0


async def _get_ws_user(
    websocket: WebSocket,
    session_repo: SessionRepository = Depends(get_session_repo),
) -> dict:
    user = await run_sync(
        identity.resolve_session_user_from_cookies, session_repo, websocket.cookies
    )
    if not user:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    return user


@router.websocket("/api/ws")
async def ws_endpoint(
    websocket: WebSocket,
    user: dict = Depends(_get_ws_user),
    event_repo: EventRepository = Depends(get_event_repo),
    session_repo: SessionRepository = Depends(get_session_repo),
) -> None:
    # Bound for the whole connection, sender task included: asyncio copies the current
    # context into a new task, so every line this connection emits — outbox reads and
    # ws_read_error included — carries the same conn_id without threading it through.
    # Same mechanism LoggingMiddleware uses to tag an HTTP request.
    conn_id = generate(size=8)
    with logger.contextualize(conn_id=conn_id):
        started = time.monotonic()
        await websocket.accept()
        logger.info("ws_connected", user_id=user["id"])

        # Cursor into the outbox, private to this connection. Starting at 0 replays
        # whatever the sweep still holds (1h), so a client that dropped — per #39 that is
        # roughly twelve times an hour — gets what it missed without any client-side
        # bookkeeping. Reads no longer consume rows, so a second tab sees them too.
        watermark = 0.0
        # Ids already sent at exactly `watermark`. The read bound has to be inclusive
        # (no monotonic cursor on this table), so this is what stops the overlapping tick
        # from being delivered twice.
        sent_at_watermark: set[str] = set()

        async def _sender() -> None:
            nonlocal watermark, sent_at_watermark
            next_revalidate = time.monotonic() + _REVALIDATE_INTERVAL_S
            while True:
                await asyncio.sleep(_POLL_INTERVAL_S)

                # Re-validate the session that authenticated the handshake, not just
                # whatever's in websocket.cookies now (those are the upgrade request's
                # cookies, fixed for the connection's life — re-reading them here just
                # avoids threading a `token` local through the closure). #79: without
                # this, logging out or expiry never stopped an already-open socket.
                if time.monotonic() >= next_revalidate:
                    next_revalidate = time.monotonic() + _REVALIDATE_INTERVAL_S
                    current = await run_sync(
                        identity.resolve_session_user_from_cookies,
                        session_repo,
                        websocket.cookies,
                    )
                    if current is None:
                        logger.info("ws_session_revoked", user_id=user["id"])
                        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                        return

                try:
                    events = await run_sync(
                        event_repo.read_since,
                        user["id"],
                        _WS_KINDS,
                        watermark,
                        sent_at_watermark,
                    )
                except Exception as e:
                    logger.opt(exception=e).warning("ws_read_error", user_id=user["id"])
                    continue
                for event in events:
                    # Advance only after the send lands: if the socket dies mid-loop the
                    # cursor stays put and the next connection re-delivers, instead of the
                    # event being lost the way a delete-on-read left it.
                    await websocket.send_json(json.loads(event.payload))
                    if event.created_at > watermark:
                        watermark = event.created_at
                        sent_at_watermark = {event.id}
                    else:
                        sent_at_watermark.add(event.id)

        async def _receiver() -> None:
            # Just a disconnect signal for the wait() below — client->server messages
            # aren't part of this protocol. Suppressed here (not left to propagate)
            # because a client-initiated close is the expected, common exit path, not
            # an error.
            with contextlib.suppress(WebSocketDisconnect):
                while True:
                    await websocket.receive_text()

        # Raced rather than "spawn sender, block the outer coroutine on receive_text()":
        # _sender.close() only sends a close frame, it doesn't itself unblock a pending
        # receive_text() (that's the ASGI receive channel, driven by the transport, not
        # by our own send). Whichever task ends first, the other is cancelled below —
        # the same first-completed/cancel-the-loser shape Starlette itself uses
        # internally to race a response stream against disconnect detection.
        sender = asyncio.create_task(_sender())
        receiver = asyncio.create_task(_receiver())
        try:
            await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sender.cancel()
            receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect):
                await sender
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect):
                await receiver
            logger.info(
                "ws_disconnected",
                user_id=user["id"],
                duration_s=round(time.monotonic() - started, 1),
            )
